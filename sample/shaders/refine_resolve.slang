#include "common.slang"
#include "scene.slang"
#include "lpv.slang"
uniform Grid g_grid;
uniform uint g_bounces;
uniform float g_lighting_scale;
StructuredBuffer<SHCell> g_front;
RWStructuredBuffer<SHCell> g_accumulated;
[shader("compute")]
[numthreads(4,4,4)]
void compute_main(uint3 tid: SV_DispatchThreadID)
{
    if(any(tid>=g_grid.resolution)) return;
    uint index=g_grid.index(int3(tid)), count=g_grid.resolution*g_grid.resolution*g_grid.resolution;
    SHCell total={};
    for(uint q=0;q<g_bounces;++q) total=sh_add(total,g_front[q*count+index]);
    g_accumulated[index]=sh_scale(total,g_lighting_scale);
}
