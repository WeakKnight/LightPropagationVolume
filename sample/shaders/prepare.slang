#include "common.slang"
#include "scene.slang"
#include "lpv.slang"
uniform Grid g_grid;
uniform uint g_bounces;
StructuredBuffer<SHCell> g_source;
RWStructuredBuffer<SHCell> g_front;
RWStructuredBuffer<SHCell> g_accumulated;
[shader("compute")]
[numthreads(4,4,4)]
void compute_main(uint3 tid : SV_DispatchThreadID)
{
    if (any(tid >= g_grid.resolution)) return;
    uint index = g_grid.index(int3(tid)), count = g_grid.resolution*g_grid.resolution*g_grid.resolution;
    g_front[index] = g_accumulated[index] = g_source[index];
    for (uint bounce=1; bounce<g_bounces; ++bounce)
        g_front[bounce*count+index] = (SHCell)0;
}
