using System;
using System.Linq;
using Zhanguo.Desktop.Layout;

static class RoadChecks
{
    private static void Require(bool value,string why){if(!value)throw new Exception(why);}
    public static void Run()
    {
        var input=new SampleInput { Seed=73,LayoutVersion=2,LiveWorld=true };
        for(int y=0;y<3;y++)for(int x=0;x<3;x++)
        {
            var region=new RegionInput { X=x,Y=y,Name=$"{x},{y}",Terrain="plain",Owner="qin" };
            if(y==1&&x>=1)
            {
                region.Buildings.Add(new BuildingInput { Id=$"{x}01",Art="native:town-hall" });
                region.Buildings.Add(new BuildingInput { Id=$"{x}02",Art="native:farm" });
            }
            input.Regions.Add(region);
        }
        var layout=new RegionLayout(input);
        Require(layout.Roads.Count>10,"street network did not connect facility entrances");
        foreach(var region in layout.Regions)
        foreach(var point in layout.Roads.For(region))
        {
            int height=PixelTerrain.HeightTier(layout.Elevation(region.Input.X*256+point.X,region.Input.Y*256+point.Y));
            var rect=new Footprint(point.X-4,point.Y-4-height*8,8,8);
            Require(layout.Water(region.Input.X*256+point.X,region.Input.Y*256+point.Y)<.15f,"road crosses water");
            Require(!region.Structures.Any(s=>RegionLayout.StructureVisualBounds(s).Overlaps(rect,1))&&!region.TownLots.Any(l=>RegionLayout.TownVisualBounds(l).Overlaps(rect,1)),"road crosses a building, roof or market");
            int adjacent=layout.Roads.For(region).Count(q=>Math.Abs(q.X-point.X)+Math.Abs(q.Y-point.Y)==8);
            Require(adjacent<=3,"street network contains an arbitrary four-way intersection");
        }
        // A deliberately open two-farm fixture proves shared edge matching;
        // a dense city may correctly leave a blocked connection unbuilt.
        var open=SampleInput.Parse(System.Text.Json.JsonSerializer.Serialize(input));
        foreach(var region in open.Regions)
        {
            region.Buildings.Clear();
            if(region.Y==1&&region.X>=1)region.Buildings.Add(new BuildingInput { Id=region.X.ToString(),Art="native:farm" });
        }
        var joined=new RegionLayout(open);
        var left=joined.Regions.Single(r=>r.Input.X==1&&r.Input.Y==1);
        var right=joined.Regions.Single(r=>r.Input.X==2&&r.Input.Y==1);
        var exits=joined.Roads.For(left).Where(p=>p.X==252).ToArray();
        Require(exits.Length>0&&exits.All(p=>joined.Roads.For(right).Any(q=>q.X==4&&q.Y==p.Y)),
            $"adjacent open streets have unmatched border portals ({joined.Roads.For(left).Count}/{joined.Roads.For(right).Count}, exits {exits.Length})");
        var fresh=new RegionLayout(input);var same=new RegionLayout(input,layout);
        foreach(var region in layout.Regions)
        {
            Require(layout.Roads.For(region).SequenceEqual(fresh.Roads.For(region)),"identical inputs changed a street route");
            Require(ReferenceEquals(layout.Roads.For(region),same.Roads.For(region)),"unchanged street arrays were rebuilt");
        }
        Console.WriteLine("ROAD_NETWORK_OK: real entrances, terrain-safe streets, paired portals, deterministic routing and reused drawing data");
    }
}
