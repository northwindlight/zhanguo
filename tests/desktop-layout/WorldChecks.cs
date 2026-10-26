using System;
using System.Linq;
using System.Text.Json.Nodes;
using Zhanguo.Desktop.Layout;

static class WorldChecks
{
    private static void Require(bool ok,string message) { if(!ok)throw new Exception(message); }
    public static void Run()
    {
        Require(DesktopScene.NationOpacity(.12f)>.6f&&DesktopScene.NationOpacity(.95f)<.15f,"political colors did not fade with zoom");
        Require(DesktopScene.SquadFigures(1)>=8&&DesktopScene.SquadFigures(100)<=48,"army icons were not replaced with bounded squads");
        Require(DesktopScene.PriceText(.000001)!="0.00"&&double.Parse(DesktopScene.PriceText(.000001),System.Globalization.CultureInfo.InvariantCulture)>0,"positive tiny quote rendered as zero");
        Require(DesktopScene.PriceText(null)=="无货","sold-out quote indistinguishable from a low price");
        var tiles=new JsonArray();
        for(int y=0;y<5;y++)for(int x=0;x<5;x++)
        {
            string terrain=x==0||y==0||x==4||y==4?"ocean":x==1&&y==1?"high-mountain":x==2&&y==1?"forest":x==3&&y==3?"mountain":"plain";
            var buildings=new JsonArray();
            if(x==2&&y==2)
                foreach(var kind in new[]{"native:town-hall","native:farm","native:gold-mine"})buildings.Add(new JsonArray(kind,1));
            tiles.Add(new JsonObject { ["position"]=new JsonArray(x,y),["name"]=$"r{x}-{y}",["terrain"]="native:"+terrain,
                ["passable"]=terrain is not "ocean" and not "high-mountain",["buildings"]=buildings,
                ["resources"]=new JsonArray(new JsonArray("native:wood",terrain=="forest"?5:0),new JsonArray("native:gold",terrain=="mountain"?2:0),new JsonArray("native:ore",x==2&&y==2?.7:0),new JsonArray("native:oil",x==2&&y==2?.4:0)) });
        }
        var frame=new JsonObject { ["map_seed"]=20261023,["tiles"]=tiles };
        var input=WorldLayoutProjection.FromFrame(frame);var layout=new RegionLayout(input);
        Require(input.LiveWorld&&input.Seed==20261023&&layout.Regions.Count==25,"live observer map was not projected");
        Require(layout.Regions.Sum(r=>r.Structures.Count)==3,"observer facilities were dropped or invented");
        var capital=layout.Regions.Single(r=>r.Structures.Any(s=>s.Art=="native:town-hall"));
        var civic=capital.Structures.Single(s=>s.Art=="native:town-hall");
        Require(civic.Bounds.Width>=48&&RegionLayout.StructureVisualBounds(civic).Height>=60,"civic hall did not become larger and taller");
        Require(capital.TownLots.Count==1&&capital.TownLots[0].Kind=="market"&&capital.Plaza!=null,"capital needs one market and no decorative housing");
        Require(capital.Input.Oil>0&&capital.OilPatches.Count>0,"real oil abundance has no surface bitumen");
        Require(capital.Input.Ore>0&&capital.Iron.Count>0,"real ore abundance has no iron deposit markers");
        Require(layout.Regions.Where(r=>r.Input.Ore==0).All(r=>r.Iron.Count==0),"iron invented on a zero-ore tile");
        foreach(var iron in capital.Iron)
        {
            var bounds=RegionLayout.RockBounds(iron);
            Require(!capital.Gold.Any(g=>RegionLayout.GoldBounds(g).Overlaps(bounds,2))&&!capital.Rocks.Any(g=>RegionLayout.RockBounds(g).Overlaps(bounds,2)),"iron marker covers gold or stone");
            Require(!capital.Structures.Any(s=>s.Bounds.Overlaps(bounds,2))&&!capital.OilPatches.Any(p=>p.Overlaps(bounds,2)),"iron marker covers buildings or bitumen");
            Require(!capital.People.Any(p=>new Footprint(p.X-3,p.Y-8,6,8).Overlaps(bounds,2)),"resident spawned inside iron deposit");
        }
        Require(layout.Regions.Where(r=>r.Input.Oil==0).All(r=>r.OilPatches.Count==0),"bitumen invented on a zero-oil tile");
        foreach(var patch in capital.OilPatches)
        {
            Require(layout.CanBuild(capital.Input,patch)&&!capital.Structures.Any(s=>s.Bounds.Overlaps(patch,2)),"oil patch covers a building or barrier");
            Require(!capital.TownLots.Any(l=>l.Bounds.Overlaps(patch,2))&&!capital.Gold.Any(g=>RegionLayout.GoldBounds(g).Overlaps(patch,2)),"oil patch covers town scenery or gold");
        }
        foreach(var lot in capital.TownLots)
        {
            Require(!capital.Structures.Any(s=>s.Bounds.Overlaps(lot.Bounds,3)),"town scenery hides a rule building");
            Require(!capital.TownLots.Any(other=>other!=lot&&other.Bounds.Overlaps(lot.Bounds,3)),"town houses overlap");
            Require(layout.CanBuild(capital.Input,lot.Bounds),"town scenery crosses a natural barrier");
        }
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
        var live=frame.DeepClone().AsObject();
        live["nations"]=JsonNode.Parse("[{\"id\":\"scenario:qin\",\"stocks\":[],\"units\":{}}]");
        live["scene"]=JsonNode.Parse("""
            {"buildings":[{"id":7,"kind":"native:farm","label":"农场","level":1,"remaining":1,"x":2,"y":2,"owner":"scenario:qin"}],
             "ownership":[{"x":2,"y":2,"owner":"scenario:qin"}],"armies":[{"id":1,"owner":"scenario:qin","kind":"native:infantry","hp":100,"x":2,"y":2}],
             "guards":[],"accounts":[{"id":"scenario:qin","stocks":[{"id":"native:gold","name":"金","amount":123}]}],"market":[]}
            """);
        DesktopScene.Synchronize(live);
        var site=live["tiles"]!.AsArray().Single(t=>t!["name"]!.ToString()=="r2-2")!;
        Require(site["building_instances"]!.AsArray().Count==1&&site["owner"]!.ToString()=="scenario:qin","initial scene facts were not projected atomically");
        Require(live["nations"]![0]!["stocks"]![0]!["amount"]!.GetValue<int>()==123,"receipt inventory delta did not reach the panel");
        var scaffold=new RegionLayout(WorldLayoutProjection.FromFrame(live));
        Require(scaffold.Regions.Sum(r=>r.People.Count)>0&&scaffold.Regions.SelectMany(r=>r.Structures).Single().Remaining==1,"residents or construction state missing");
        var delta=JsonNode.Parse("""
            {"buildings":{"remove":[],"upsert":[{"id":7,"kind":"native:farm","label":"农场","level":1,"remaining":0,"x":2,"y":2,"owner":"scenario:qin"}]}}
            """)!.AsObject();
        DesktopScene.Apply(live,delta);
        var completed=new RegionLayout(WorldLayoutProjection.FromFrame(live),scaffold);
        Require(completed.Regions.SelectMany(r=>r.Structures).Single().Remaining==0,"completion delta left a scaffold");
        Require(completed.Regions.SelectMany(r=>r.Structures).Single().Bounds==scaffold.Regions.SelectMany(r=>r.Structures).Single().Bounds,"building jumped at completion");
        Require(completed.GeometryKey==scaffold.GeometryKey,"construction animation invalidated terrain");
        Console.WriteLine("DESKTOP_SCENE_OK: political zoom, squads, tiny prices, atomic deltas, residents and stable construction");
        Console.WriteLine("LIVE_WORLD_LAYOUT_OK: observer projection, barriers, facility counts, local updates and surface keys");
    }
}
