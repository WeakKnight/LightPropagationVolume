// Direction-preserving volume transport: boundary radiance is
// advected through empty cells without re-emitting a cosine lobe in the air.
#include "common.slang"
#include "scene.slang"
#include "lpv.slang"
#include "probe_visibility.slang"
uniform Grid g_grid;
uniform Scene g_scene;
uniform Sun g_sun;
uniform uint g_direction_count;
uniform uint g_bounce;
uniform float g_decay;
uniform uint g_source_samples;
StructuredBuffer<float4> g_angular_samples;
StructuredBuffer<int4> g_offsets;
StructuredBuffer<float4> g_directions; // Unit travel direction, solid-angle weight.
RWStructuredBuffer<float4> g_hits; // Surface normal and ray distance; -1 outside, 0 miss.
RWStructuredBuffer<uint> g_material;
RWStructuredBuffer<float4> g_boundary;
RWStructuredBuffer<float4> g_direct;
StructuredBuffer<float4> g_previous_angular;
StructuredBuffer<float4> g_front;
StructuredBuffer<float> g_irradiance_filter;
RWStructuredBuffer<float4> g_previous_irradiance;
StructuredBuffer<float4> g_previous_irradiance_read;
RWStructuredBuffer<float4> g_total_irradiance;
RWStructuredBuffer<float4> g_next;
RWStructuredBuffer<SHCell> g_source_projected;
RWStructuredBuffer<SHCell> g_total;
RWStructuredBuffer<float4> g_angular_total;
uniform bool g_store_angular_total;

Hit boundary_hit(uint cell, uint direction)
{
    uint slot=cell*g_direction_count+direction;
    float4 entry=g_hits[slot];
    uint primitive=g_material[slot];
    uint3 ids=g_scene.triangles[primitive];
    int3 p=int3(cell%g_grid.resolution,(cell/g_grid.resolution)%g_grid.resolution,
        cell/(g_grid.resolution*g_grid.resolution));
    Hit hit={}; hit.valid=true;
    hit.position=g_grid.center(p)-g_directions[direction].xyz*entry.w;
    hit.normal=entry.xyz;
    float3 a=g_scene.positions[ids.x], b=g_scene.positions[ids.y], c=g_scene.positions[ids.z];
    hit.geometric_normal=normalize(cross(b-a,c-a));
    if (dot(hit.geometric_normal,g_directions[direction].xyz)<0) hit.geometric_normal=-hit.geometric_normal;
    hit.albedo=g_scene.albedos[primitive].xyz;
    hit.emission=g_scene.emissions[primitive].xyz;
    return hit;
}

float3 previous_irradiance(Hit hit)
{
    float3 point=(hit.position-g_grid.origin)/g_grid.cell_size-.5;
    int3 base=int3(floor(point)); float3 f=frac(point);
    float3 result=0;float sum=0;
    for (int z=0;z<2;++z) for(int y=0;y<2;++y) for(int x=0;x<2;++x)
    {
        int3 p=base+int3(x,y,z);if(!g_grid.contains(p))continue;
        float w=(x!=0?f.x:1-f.x)*(y!=0?f.y:1-f.y)*(z!=0?f.z:1-f.z);
        w*=probe_visibility_weight(g_grid,g_scene,p,hit);
        if(w<=0)continue;
        float3 irradiance=0;
        if(g_probe_visibility==0)
            irradiance=sample_probe_irradiance(g_previous_irradiance_read,g_grid.index(p),hit.normal);
        else
        {
            float cosine_sum=0;
            for(uint direction=0;direction<g_direction_count;++direction)
            {
                float4 d=g_directions[direction];float cosine=max(dot(-d.xyz,hit.normal),0)*d.w;
                irradiance+=g_previous_angular[g_grid.index(p)*g_direction_count+direction].xyz*cosine;
                cosine_sum+=cosine;
            }
            irradiance*=PI/cosine_sum;
        }
        result+=w*irradiance;sum+=w;
    }
    return sum>0 ? result/sum : float3(0);
}

[shader("compute")][numthreads(64,1,1)]
void cache_main(uint3 tid:SV_DispatchThreadID)
{
    uint cells=g_grid.resolution*g_grid.resolution*g_grid.resolution;
    uint slot=tid.x;if(slot>=cells*g_direction_count)return;
    uint cell=slot/g_direction_count, direction=slot%g_direction_count;
    int3 p=int3(cell%g_grid.resolution,(cell/g_grid.resolution)%g_grid.resolution,
        cell/(g_grid.resolution*g_grid.resolution));
    float3 offset=float3(g_offsets[direction].xyz)*g_grid.cell_size;
    Hit hit=g_scene.intersect(g_grid.center(p),-g_directions[direction].xyz,length(offset),g_scene.ray_epsilon);
    // A boundary outside the grid can still contribute if the segment hits it.
    float distance=hit.valid ? length(hit.position-g_grid.center(p)) :
        (g_grid.contains(p-g_offsets[direction].xyz)?0.0:-1.0);
    g_hits[slot]=float4(hit.normal,distance);
    g_material[slot]=hit.valid ? hit.primitive : 0;
}

[shader("compute")][numthreads(64,1,1)]
void direct_main(uint3 tid:SV_DispatchThreadID)
{
    uint cells=g_grid.resolution*g_grid.resolution*g_grid.resolution;
    uint slot=tid.x;if(slot>=cells*g_direction_count)return;
    uint cell=slot/g_direction_count,direction=slot%g_direction_count;
    int3 p=int3(cell%g_grid.resolution,(cell/g_grid.resolution)%g_grid.resolution,
        cell/(g_grid.resolution*g_grid.resolution));
    float3 value=0;
    for(uint sample=0;sample<g_source_samples;++sample)
    {
        float3 travel=g_angular_samples[direction*g_source_samples+sample].xyz;
        Hit hit=g_scene.intersect(g_grid.center(p),-travel,1e30,g_scene.ray_epsilon);
        if(hit.valid)
        {
            float distance_in_cells=length(hit.position-g_grid.center(p))/g_grid.cell_size;
            value+=(hit.emission+hit.albedo*INV_PI*direct_irradiance(g_scene,hit,g_sun))
                *pow(g_decay,distance_in_cells);
        }
    }
    g_direct[slot]=float4(value/float(g_source_samples),0);
}

[shader("compute")][numthreads(64,1,1)]
void copy_direct_main(uint3 tid:SV_DispatchThreadID)
{
    uint cells=g_grid.resolution*g_grid.resolution*g_grid.resolution;
    if(tid.x<cells*g_direction_count)g_next[tid.x]=g_direct[tid.x];
}

[shader("compute")][numthreads(64,1,1)]
void inject_main(uint3 tid:SV_DispatchThreadID)
{
    uint cells=g_grid.resolution*g_grid.resolution*g_grid.resolution;
    uint slot=tid.x;if(slot>=cells*g_direction_count)return;
    float3 value=g_bounce==0 ? g_direct[slot].xyz : float3(0);
    if(g_bounce>0 && g_hits[slot].w>0)
    {
        Hit hit=boundary_hit(slot/g_direction_count,slot%g_direction_count);
        value=hit.albedo*INV_PI*previous_irradiance(hit);
    }
    g_boundary[slot]=float4(value,0);
}

[shader("compute")][numthreads(64,1,1)]
void clear_main(uint3 tid:SV_DispatchThreadID)
{
    uint cells=g_grid.resolution*g_grid.resolution*g_grid.resolution;
    if(tid.x<cells*g_direction_count)g_next[tid.x]=0;
}

[shader("compute")][numthreads(64,1,1)]
void step_main(uint3 tid:SV_DispatchThreadID)
{
    uint cells=g_grid.resolution*g_grid.resolution*g_grid.resolution;
    uint slot=tid.x;if(slot>=cells*g_direction_count)return;
    uint cell=slot/g_direction_count,direction=slot%g_direction_count;
    int3 p=int3(cell%g_grid.resolution,(cell/g_grid.resolution)%g_grid.resolution,
        cell/(g_grid.resolution*g_grid.resolution));
    float distance=g_hits[slot].w;
    float4 value=0;
    if(distance>0)value=g_boundary[slot];
    else if(distance==0)value=g_front[g_grid.index(p-g_offsets[direction].xyz)*g_direction_count+direction];
    float length_in_cells=distance>0 ? distance/g_grid.cell_size : length(float3(g_offsets[direction].xyz));
    g_next[slot]=value*pow(g_decay,length_in_cells);
}

[shader("compute")][numthreads(64,1,1)]
void project_main(uint3 tid:SV_DispatchThreadID)
{
    uint cells=g_grid.resolution*g_grid.resolution*g_grid.resolution;
    uint cell=tid.x;if(cell>=cells)return;
    SHCell projected={}, injected={};
    for(uint i=0;i<g_direction_count;++i)
    {
        uint slot=cell*g_direction_count+i;
        if(g_store_angular_total)
            g_angular_total[slot]=g_bounce==0 ? g_front[slot] : g_angular_total[slot]+g_front[slot];
        float4 d=g_directions[i];float3 value=g_front[slot].xyz*d.w;
        SHCell delta={};
        float4 lo=sh_basis(d.xyz), hi=sh_basis_l2(d.xyz);float last=sh_basis_last(d.xyz);
        delta.r=value.r*lo;delta.g=value.g*lo;delta.b=value.b*lo;
        delta.r2=value.r*hi;delta.g2=value.g*hi;delta.b2=value.b*hi;delta.last=float4(value*last,0);
        projected=sh_add(projected,delta);
        if(g_bounce==0)
        {
            float3 source=g_direct[slot].xyz*d.w;
            delta.r=source.r*lo;delta.g=source.g*lo;delta.b=source.b*lo;
            delta.r2=source.r*hi;delta.g2=source.g*hi;delta.b2=source.b*hi;delta.last=float4(source*last,0);
            injected=sh_add(injected,delta);
        }
    }
    if(g_bounce==0)g_source_projected[cell]=injected;
    g_total[cell]=g_bounce==0 ? projected : sh_add(g_total[cell],projected);
}

// Cache the cosine integral once per probe/normal, rather than once per query.
[shader("compute")][numthreads(64,1,1)]
void irradiance_main(uint3 tid:SV_DispatchThreadID)
{
    uint cells=g_grid.resolution*g_grid.resolution*g_grid.resolution;
    uint texels=(g_irradiance_size+2)*(g_irradiance_size+2);
    uint slot=tid.x;if(slot>=cells*texels)return;
    uint cell=slot/texels,texel=slot%texels;
    float3 value=0;
    for(uint i=0;i<g_direction_count;++i)
        value+=g_front[cell*g_direction_count+i].xyz*g_irradiance_filter[texel*g_direction_count+i];
    g_previous_irradiance[slot]=float4(value,0);
    g_total_irradiance[slot]=g_bounce==0 ? float4(value,0) : g_total_irradiance[slot]+float4(value,0);
}
