#include "common.slang"
#include "scene.slang"
#include "lpv.slang"
uniform Grid g_grid;
uniform uint g_bounces;
uniform float g_history_weight;
uniform bool g_reset;
StructuredBuffer<SHCell> g_target;
RWStructuredBuffer<SHCell> g_front;
RWStructuredBuffer<SHCell> g_accumulated;
[shader("compute")][numthreads(4,4,4)]
void compute_main(uint3 tid:SV_DispatchThreadID)
{
    if(any(tid>=g_grid.resolution))return;
    uint cell=g_grid.index(int3(tid)),count=g_grid.resolution*g_grid.resolution*g_grid.resolution;
    SHCell total={};
    for(uint q=0;q<g_bounces;++q)
    {
        uint slot=q*count+cell;
        SHCell old=g_reset ? (SHCell)0 : g_front[slot];
        SHCell current=sh_add(sh_scale(old,g_history_weight),sh_scale(g_target[slot],1-g_history_weight));
        g_front[slot]=current;
        total=sh_add(total,current);
    }
    g_accumulated[cell]=total;
}
