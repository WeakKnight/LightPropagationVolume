#include "common.slang"
#include "scene.slang"
#include "lpv.slang"
#include "geometry_volume.slang"
struct GeometrySample { float4 position_area; float4 normal; float4 albedo; float4 emission; };
uniform Grid g_grid;
StructuredBuffer<GeometrySample> g_samples;
StructuredBuffer<uint2> g_ranges;
RWStructuredBuffer<GeometryCell> g_geometry;
[shader("compute")]
[numthreads(4,4,4)]
void compute_main(uint3 tid: SV_DispatchThreadID)
{
    uint n=g_grid.resolution+1;
    if(any(tid>=n)) return;
    uint index=geometry_index(int3(tid),n);
    uint2 range=g_ranges[index];
    GeometryCell cell={};
    float area=0;
    for(uint i=0;i<range.y;++i)
    {
        GeometrySample sample=g_samples[range.x+i];
        float a=sample.position_area.w;
        cell.occlusion+=sh_basis(sample.normal.xyz)*(a/(g_grid.cell_size*g_grid.cell_size));
        cell.material.xyz+=sample.albedo.xyz*a;
        area+=a;
    }
    cell.material.xyz/=max(area,1e-20);
    cell.material.w=area;
    g_geometry[index]=cell;
}
