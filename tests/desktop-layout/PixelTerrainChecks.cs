using System;
using System.Linq;
using Zhanguo.Desktop.Layout;

static class PixelTerrainChecks
{
    static void Require(bool value,string why){if(!value)throw new Exception(why);}
    public static void Run(RegionLayout layout)
    {
        var terrain=new PixelTerrain(layout);
        Require(terrain.Width==48&&terrain.Height==48,"visual grid must be exactly 16 cells per rule region");
        Require(terrain.MountainCells>0,"mountain fixture lost its discrete heights");
        for(int y=0;y<terrain.Height;y++)for(int x=0;x<terrain.Width;x++)
        {
            var c=terrain.At(x,y);
            Require(c.Height is >=0 and <=4&&c.Water is >=0 and <=3,"invalid terrain tier");
            if(x+1<terrain.Width&&c.Height==terrain.At(x+1,y).Height)
                Require((terrain.LowerMask(x,y)&2)==0&&(terrain.LowerMask(x+1,y)&8)==0,"equal-height shared edge invented a wall");
        }
        var input=SampleInput.Parse(System.Text.Json.JsonSerializer.Serialize(layout.Input));
        foreach(var r in input.Regions){r.Buildings.Clear();r.People=0;}
        var cleared=new PixelTerrain(new RegionLayout(input));
        for(int y=0;y<terrain.Height;y++)for(int x=0;x<terrain.Width;x++)
            Require(terrain.At(x,y)==cleared.At(x,y),"building changes must not regenerate terrain or coastline");
        input.LiveWorld=true;
        foreach(var r in input.Regions){r.Terrain="ocean";r.Passable=false;}
        var ocean=new PixelTerrain(new RegionLayout(input));
        Require(ocean.WaterCells==ocean.Width*ocean.Height&&ocean.MountainCells==0,"ocean contains invented traversable visual islands");
        Console.WriteLine("PIXEL_TERRAIN_OK: discrete heights, seamless masks, immutable terrain, continuous ocean");
    }
}
