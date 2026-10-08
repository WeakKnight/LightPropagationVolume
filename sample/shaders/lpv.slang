// Real SH basis, light-travel directions. L1 uses the same signs as the MVP.
// L2 adds five directional coefficients, as in UE4's nine-coefficient LPV.
float4 sh_basis(float3 d)
{
    return float4(.2820947918, -.4886025119*d.y, .4886025119*d.z, -.4886025119*d.x);
}
float4 sh_basis_l2(float3 d)
{
    return float4(1.092548431*d.x*d.y, -1.092548431*d.y*d.z,
        .3153915653*(3*d.z*d.z-1), -1.092548431*d.x*d.z);
}
float sh_basis_last(float3 d) { return .5462742153*(d.x*d.x-d.y*d.y); }
float4 cosine_lobe(float3 n)
{
    return float4(.8862269255, -1.023326708*n.y, 1.023326708*n.z, -1.023326708*n.x);
}
// Cosine convolution's band-2 eigenvalue is PI/4.
struct SHCell
{
    float4 r; float4 g; float4 b;
    float4 r2; float4 g2; float4 b2;
    float4 last; // RGB coefficient 8 plus one padding float; 112-byte portable stride.
};
SHCell sh_scale(SHCell a, float w)
{
    SHCell result = {a.r*w,a.g*w,a.b*w,a.r2*w,a.g2*w,a.b2*w,a.last*w};
    return result;
}
SHCell sh_add(SHCell a, SHCell b)
{
    SHCell result = {a.r+b.r,a.g+b.g,a.b+b.b,a.r2+b.r2,a.g2+b.g2,a.b2+b.b2,a.last+b.last};
    return result;
}
SHCell sh_from_lobe(float3 value, float3 normal)
{
    float4 lo = cosine_lobe(normal), hi = sh_basis_l2(normal)*(.25*PI);
    SHCell result = {value.r*lo,value.g*lo,value.b*lo,value.r*hi,value.g*hi,value.b*hi,
        float4(value*sh_basis_last(normal)*(.25*PI),0)};
    return result;
}
// Project a directional contribution once, without an extra cosine convolution.
SHCell sh_from_direction(float3 value, float3 direction)
{
    float4 lo=sh_basis(direction), hi=sh_basis_l2(direction);
    SHCell result={value.r*lo,value.g*lo,value.b*lo,value.r*hi,value.g*hi,value.b*hi,
        float4(value*sh_basis_last(direction),0)};
    return result;
}
SHCell sh_color_scale(SHCell a, float3 value)
{
    SHCell result = {a.r*value.r,a.g*value.g,a.b*value.b,
        a.r2*value.r,a.g2*value.g,a.b2*value.b,float4(a.last.xyz*value,0)};
    return result;
}
float3 sh_dot(SHCell cell, float4 lo, float4 hi, float last)
{
    return float3(dot(cell.r,lo)+dot(cell.r2,hi),dot(cell.g,lo)+dot(cell.g2,hi),
        dot(cell.b,lo)+dot(cell.b2,hi)) + cell.last.xyz*last;
}
float3 sh_evaluate(SHCell cell, float3 direction)
{
    return max(sh_dot(cell,sh_basis(direction),sh_basis_l2(direction),sh_basis_last(direction)),0.0);
}
float3 sh_irradiance(SHCell cell, float3 direction)
{
    return max(sh_dot(cell,cosine_lobe(direction),sh_basis_l2(direction)*(.25*PI),
        sh_basis_last(direction)*(.25*PI)),0.0);
}

struct Grid
{
    float3 origin;
    float cell_size;
    uint resolution;

    uint index(int3 p) { return uint(p.x + resolution * (p.y + resolution * p.z)); }
    bool contains(int3 p) { return all(p >= 0) && all(p < int(resolution)); }
    float3 center(int3 p) { return origin + (float3(p) + .5) * cell_size; }
};

static const int3 AXES[6] = {
    int3(1,0,0), int3(-1,0,0), int3(0,1,0), int3(0,-1,0), int3(0,0,1), int3(0,0,-1)
};

struct Sun
{
    float3 direction;
    float3 color;
    float intensity; // Perpendicular irradiance of an ideal directional light.
};

float3 direct_irradiance(Scene scene, Hit hit, Sun sun)
{
    float cosine = max(dot(hit.normal, sun.direction), 0.0);
    if (sun.intensity <= 0 || cosine <= 0 || dot(hit.geometric_normal, sun.direction) <= 0)
        return 0;
    return scene.visible(scene.offset_origin(hit), sun.direction) ? sun.color * sun.intensity * cosine : 0;
}
