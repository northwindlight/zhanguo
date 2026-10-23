using System;
using System.Linq;
using System.Text.Json.Nodes;
using Zhanguo.Desktop.Layout;

static class WorldChecks
{
    private static void Require(bool ok,string message) { if(!ok)throw new Exception(message); }
    public static void Run()
    {
        var tiles=new JsonArray();
        for(int y=0;y<5;y++)for(int x=0;x<5;x++)
        {
            string terrain=x==0||y==0||x==4||y==4?"ocean":x==1&&y==1?"high-mountain":x==2&&y==1?"forest":x==3&&y==3?"mountain":"plain";
            var buildings=new JsonArray();
            if(x==2&&y==2)
                foreach(var kind in new[]{"native:town-hall","native:farm","native:gold-mine"})buildings.Add(new JsonArray(kind,1));
            tiles.Add(new JsonObject { ["position"]=new JsonArray(x,y),["name"]=$"r{x}-{y}",["terrain"]="native:"+terrain,
                ["passable"]=terrain is not "ocean" and not "high-mountain",["buildings"]=buildings,
                ["resources"]=new JsonArray(new JsonArray("native:wood",terrain=="forest"?5:0),new JsonArray("native:gold",terrain=="mountain"?2:0)) });
        }
        var frame=new JsonObject { ["map_seed"]=20261023,["tiles"]=tiles };
        var input=WorldLayoutProjection.FromFrame(frame);var layout=new RegionLayout(input);
        Require(input.LiveWorld&&input.Seed==20261023&&layout.Regions.Count==25,"live observer map was not projected");
        Require(layout.Regions.Sum(r=>r.Structures.Count)==3,"observer facilities were dropped or invented");
        Require(input.Regions.Single(r=>r.Terrain=="forest").Wood==100,"real wood capacity was not normalized");
        foreach(var r in layout.Regions)
        {
            if(!r.Input.Passable)Require(r.Structures.Count+r.Trees.Count+r.Rocks.Count+r.People.Count==0,"decorations placed on a natural barrier");
            foreach(var t in r.Trees)Require(layout.Water(r.Input.X*256+t.Foot.X,r.Input.Y*256+t.Foot.Y)<.15f,"tree placed in ocean");
            foreach(var b in r.Structures)Require(layout.CanBuild(r.Input,b.Bounds),"live facility crossed a barrier, shoreline or region margin");
        }
        var high=input.Regions.Single(r=>r.Terrain=="high-mountain");
        Require(layout.Elevation(high.X*256+128,high.Y*256+128)>.4f,"high mountains look like ordinary flat land");
        var same=new RegionLayout(WorldLayoutProjection.FromFrame(frame),layout);
        Require(layout.GeometryKey==same.GeometryKey&&layout.Regions.Zip(same.Regions).All(p=>ReferenceEquals(p.First,p.Second)),"unchanged world rebuilt region plans");
        var town=tiles.Select(t=>t!.AsObject()).Single(t=>t["name"]!.ToString()=="r2-2");
        town["buildings"]!.AsArray().Add(new JsonArray("native:barracks",1));
        var updated=new RegionLayout(WorldLayoutProjection.FromFrame(frame),same);
        Require(updated.GeometryKey==same.GeometryKey,"a new building invalidated the terrain surface");
        Require(updated.Regions.Sum(r=>r.Structures.Count)==4,"new live building was not drawn");
        Require(updated.Regions.Zip(same.Regions).Count(p=>ReferenceEquals(p.First,p.Second))==24,"unrelated region plans were rebuilt");
        town["terrain"]="native:hill";
        var changed=new RegionLayout(WorldLayoutProjection.FromFrame(frame),updated);
        Require(changed.GeometryKey!=updated.GeometryKey,"terrain mutation failed to invalidate the surface");
        for(int i=0;i<300;i++)town["buildings"]!.AsArray().Add(new JsonArray("native:lumber-camp",1));
        var dense=new RegionLayout(WorldLayoutProjection.FromFrame(frame),changed);
        Require(dense.Regions.Sum(r=>r.Structures.Sum(s=>s.Count))==304,"dense region lost facility counts");
        Console.WriteLine("LIVE_WORLD_LAYOUT_OK: observer projection, barriers, facility counts, local updates and surface keys");
    }
}
