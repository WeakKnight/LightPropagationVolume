// RT-guided radiance injection into a compact SH field. Only one material
// order is evaluated at a time; no multi-hit reference/path tracer is invoked.
#include "common.slang"
#include "scene.slang"
#include "lpv.slang"
#include "probe_visibility.slang"
uniform Grid g_grid;
uniform Scene g_scene;
uniform Sun g_sun;
uniform uint g_stage;
uniform uint g_probe_offset;
uniform uint g_probe_count;
uniform float g_decay;
uniform float g_bounce_strength;
uniform uint g_ray_count;
StructuredBuffer<float4> g_rays; // Unit travel direction + solid angle.
StructuredBuffer<SHCell> g_previous;
RWStructuredBuffer<SHCell> g_captured;
RWStructuredBuffer<SHCell> g_source;

float3 surface_irradiance(Hit hit, uint count)
{
    float3 coordinate=(hit.position-g_grid.origin)/g_grid.cell_size-.5;
    int3 base=int3(floor(coordinate));float3 f=frac(coordinate);
    SHCell cell={};float weight_sum=0;
    for(int z=0;z<2;++z)for(int y=0;y<2;++y)for(int x=0;x<2;++x)
    {
        int3 p=base+int3(x,y,z);if(!g_grid.contains(p))continue;
        float weight=(x!=0?f.x:1-f.x)*(y!=0?f.y:1-f.y)*(z!=0?f.z:1-f.z);
        weight*=probe_visibility_weight(g_grid,g_scene,p,hit);
        if(weight<=0)continue;
        cell=sh_add(cell,sh_scale(g_previous[(g_stage-1)*count+g_grid.index(p)],weight));
        weight_sum+=weight;
    }
    return weight_sum>0 ? sh_irradiance(sh_scale(cell,1/weight_sum),-hit.normal) : float3(0);
}

[shader("compute")][numthreads(64,1,1)]
void compute_main(uint3 tid:SV_DispatchThreadID)
{
    uint count=g_grid.resolution*g_grid.resolution*g_grid.resolution;
    if(tid.x>=g_probe_count)return;
    uint cell=g_probe_offset+tid.x;if(cell>=count)return;
    int3 p=int3(cell%g_grid.resolution,(cell/g_grid.resolution)%g_grid.resolution,
        cell/(g_grid.resolution*g_grid.resolution));
    SHCell result={};
    if(g_probe_visibility!=0 || g_probe_active[cell]!=0)
    {
        float3 origin=g_grid.center(p);
        for(uint i=0;i<g_ray_count;++i)
        {
            float4 ray=g_rays[i];
            Hit hit=g_scene.intersect(origin,-ray.xyz,1e30,g_scene.ray_epsilon);
            if(!hit.valid)continue;
            float3 outgoing;
            if(g_stage==0)
                outgoing=hit.emission+hit.albedo*INV_PI*direct_irradiance(g_scene,hit,g_sun);
            else
                outgoing=hit.albedo*INV_PI*surface_irradiance(hit,count)*g_bounce_strength;
            outgoing*=pow(g_decay,length(hit.position-origin)/g_grid.cell_size);
            result=sh_add(result,sh_from_direction(outgoing*ray.w,ray.xyz));
        }
    }
    g_captured[g_stage*count+cell]=result;
    if(g_stage==0)g_source[cell]=result;
}
