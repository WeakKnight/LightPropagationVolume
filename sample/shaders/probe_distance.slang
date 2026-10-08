#include "common.slang"
#include "scene.slang"
#include "lpv.slang"
uniform Scene g_scene;
uniform Grid g_grid;
uniform uint g_distance_rays;
uniform uint g_distance_size;
uniform uint g_filter_taps;
StructuredBuffer<float4> g_ray_directions;
StructuredBuffer<uint> g_filter_indices;
StructuredBuffer<float> g_filter_weights;
RWStructuredBuffer<float> g_depths;
RWStructuredBuffer<float2> g_moments;
RWStructuredBuffer<uint> g_active;

[shader("compute")][numthreads(64,1,1)]
void trace_main(uint3 tid:SV_DispatchThreadID)
{
    uint cells=g_grid.resolution*g_grid.resolution*g_grid.resolution;
    uint slot=tid.x;if(slot>=cells*g_distance_rays)return;
    uint cell=slot/g_distance_rays;
    int3 p=int3(cell%g_grid.resolution,(cell/g_grid.resolution)%g_grid.resolution,
        cell/(g_grid.resolution*g_grid.resolution));
    float3 direction=g_ray_directions[slot%g_distance_rays].xyz;
    // Same local distance cap as DDGI: 1.5 times the probe-spacing diagonal.
    float max_distance=1.5*sqrt(3.0)*g_grid.cell_size;
    Hit hit=g_scene.intersect(g_grid.center(p),direction,max_distance,g_scene.ray_epsilon);
    float distance=hit.valid ? length(hit.position-g_grid.center(p)) : max_distance;
    if(hit.valid)
    {
        uint3 ids=g_scene.triangles[hit.primitive];
        float3 ng=cross(g_scene.positions[ids.y]-g_scene.positions[ids.x],
            g_scene.positions[ids.z]-g_scene.positions[ids.x]);
        if(dot(ng,direction)>0)distance=-distance;
    }
    g_depths[slot]=distance;
}

[shader("compute")][numthreads(64,1,1)]
void blend_main(uint3 tid:SV_DispatchThreadID)
{
    uint cells=g_grid.resolution*g_grid.resolution*g_grid.resolution;
    uint texels=(g_distance_size+2)*(g_distance_size+2);
    uint slot=tid.x;if(slot>=cells*texels)return;
    uint cell=slot/texels,texel=slot%texels;
    float2 moments=0;
    for(uint i=0;i<g_filter_taps;++i)
    {
        uint filter=texel*g_filter_taps+i;
        float d=abs(g_depths[cell*g_distance_rays+g_filter_indices[filter]]);
        moments+=float2(d,d*d)*g_filter_weights[filter];
    }
    // Store full moments; SDK's factor-of-two packing is unnecessary here.
    g_moments[slot]=moments;
}

[shader("compute")][numthreads(64,1,1)]
void classify_main(uint3 tid:SV_DispatchThreadID)
{
    uint cells=g_grid.resolution*g_grid.resolution*g_grid.resolution;
    uint cell=tid.x;if(cell>=cells)return;
    uint backfaces=0;
    for(uint i=0;i<g_distance_rays;++i)backfaces+=g_depths[cell*g_distance_rays+i]<0 ? 1 : 0;
    // Reject probes likely inside solid geometry (DDGI backface classification).
    g_active[cell]=backfaces<=g_distance_rays/4 ? 1 : 0;
}
