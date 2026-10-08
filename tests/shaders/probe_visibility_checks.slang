#include "common.slang"
#include "scene.slang"
#include "lpv.slang"
#include "probe_visibility.slang"
StructuredBuffer<float4> g_cases;
RWStructuredBuffer<float> g_results;
uniform uint g_count;
[shader("compute")][numthreads(64,1,1)]
void formula_main(uint3 tid:SV_DispatchThreadID)
{
    if(tid.x>=g_count)return;
    float4 c=g_cases[tid.x];
    g_results[tid.x]=distance_visibility_weight(c.x,c.yz);
}
