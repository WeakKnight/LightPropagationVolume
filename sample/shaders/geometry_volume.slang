// Geometry lives at grid corners. L1 stores projected surface area, RGB stores
// area-weighted diffuse reflectance. No lighting or camera enters this volume.
struct GeometryCell { float4 occlusion; float4 material; };
uint geometry_index(int3 p, uint n) { return uint(p.x+n*(p.y+n*p.z)); }

GeometryCell geometry_between(StructuredBuffer<GeometryCell> cells, int3 p, int3 offset, uint n)
{
    GeometryCell mean = {};
    uint taps = 0;
    for (int z=0; z<2; ++z) for (int y=0; y<2; ++y) for (int x=0; x<2; ++x)
    {
        int3 corner=int3(x,y,z);
        if (any((offset>0) && (corner!=1)) || any((offset<0) && (corner!=0))) continue;
        GeometryCell cell=cells[geometry_index(p+corner,n)];
        mean.occlusion+=cell.occlusion;
        mean.material+=cell.material;
        ++taps;
    }
    mean.occlusion/=float(taps);
    mean.material/=float(taps);
    return mean;
}
