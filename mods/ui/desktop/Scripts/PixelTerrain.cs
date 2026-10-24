using System;
using System.Linq;

namespace Zhanguo.Desktop.Layout;

// Discrete visual cells sampled in global coordinates, independent of camera,
// ownership and buildings. Rule passability continues to come from the world.
public sealed class PixelTerrain
{
    public int Width { get; }
    public int Height { get; }
    public readonly record struct Cell(int Height,int Water,int Variant,bool Sand);
    private readonly Cell[] _cells;
    public PixelTerrain(RegionLayout layout)
    {
        Width=layout.Width*16;Height=layout.Height*16;_cells=new Cell[Width*Height];
        for(int y=0;y<Height;y++)for(int x=0;x<Width;x++)
        {
            float wx=x*16+8,wy=y*16+8,w=layout.Water(wx,wy),h=layout.Elevation(wx,wy);
            int water=w>.76f?3:w>.40f?2:w>.15f?1:0;
            int level=HeightTier(h,water);
            _cells[y*Width+x]=new(level,water,(int)(layout.Hash(x,y,304)%3),layout.Sand(wx,wy)>.5f||w>.025f&&water==0);
        }
    }
    public static int HeightTier(float elevation,int water=0)=>water>0?0:elevation<.26f?0:elevation<.45f?1:elevation<.65f?2:elevation<.83f?3:4;
    public Cell At(int x,int y)=>x<0||y<0||x>=Width||y>=Height?new(0,3,0,false):_cells[y*Width+x];
    private static readonly (int X,int Y,int Bit)[] Neighbors={(0,-1,1),(1,0,2),(0,1,4),(-1,0,8)};
    public int LowerMask(int x,int y)
    {
        int level=At(x,y).Height,mask=0;
        foreach(var n in Neighbors)if(At(x+n.X,y+n.Y).Height<level)mask|=n.Bit;
        return mask;
    }
    public int CoastMask(int x,int y)
    {
        int mask=0;foreach(var n in Neighbors)if(At(x+n.X,y+n.Y).Water==0)mask|=n.Bit;return mask;
    }
    public int MountainCells=>_cells.Count(c=>c.Height>0);
    public int WaterCells=>_cells.Count(c=>c.Water>0);
}
