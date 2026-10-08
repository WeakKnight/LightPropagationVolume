#include "common.slang"
#include "scene.slang"
#include "lpv.slang"
uniform Grid g_grid;
uniform uint g_bounces;
uniform float g_history_weight;
uniform bool g_reset;
StructuredBuffer<SHCell> g_source;
RWStructuredBuffer<SHCell> g_front;
[shader("compute")]
[numthreads(4,4,4)]
void compute_main(uint3 tid: SV_DispatchThreadID)
{
    if(any(tid>=g_grid.resolution)) return;
    uint index=g_grid.index(int3(tid)), count=g_grid.resolution*g_grid.resolution*g_grid.resolution;
    for(uint q=0;q<g_bounces;++q)
    {
        SHCell history=g_reset ? (SHCell)0 : sh_scale(g_front[q*count+index],g_history_weight);
        g_front[q*count+index]=q==0 ? sh_add(history,g_source[index]) : history;
    }
}
