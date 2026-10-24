using System;
using System.Collections.Generic;
using System.Linq;
using System.Text.Json.Nodes;

namespace Zhanguo.Desktop.Layout;

// The observer frame is the only source of world terrain and facilities.
public static class WorldLayoutProjection
{
    public static SampleInput FromFrame(JsonObject frame)
    {
        long.TryParse(frame["map_seed"]?.ToString(),out var seed);
        var input=new SampleInput { Seed=unchecked((int)(seed^(seed>>32))),LayoutVersion=2,LiveWorld=true };
        var colors=frame["names"] is JsonObject names?names.Select((n,i)=>(n.Key,Color:DesktopScene.NationPalette[i%DesktopScene.NationPalette.Length])).ToDictionary(n=>n.Key,n=>n.Color):new Dictionary<string,string>();
        foreach(var item in frame["tiles"]!.AsArray())
        {
            var tile=item!.AsObject();string terrain=tile["terrain"]!.ToString().Split(':').Last();
            var resources=new Dictionary<string,double>();
            if(tile["resources"] is JsonArray rows)
                foreach(var row in rows)resources[row![0]!.ToString()]=double.Parse(row[1]!.ToString(),System.Globalization.CultureInfo.InvariantCulture);
            var r=new RegionInput { X=tile["position"]![0]!.GetValue<int>(),Y=tile["position"]![1]!.GetValue<int>(),
                Name=tile["name"]?.ToString()??"",Terrain=terrain,
                Passable=tile["passable"]?.GetValue<bool>()??(terrain is not "ocean" and not "high-mountain"),
                Wood=(int)Math.Clamp(resources.GetValueOrDefault("native:wood")*20,0,100),
                Gold=(int)Math.Clamp(resources.GetValueOrDefault("native:gold")*50,0,100),
                Ore=resources.GetValueOrDefault("native:ore")>0?(int)Math.Clamp(Math.Ceiling(resources["native:ore"]*50),1,100):0,
                Oil=resources.GetValueOrDefault("native:oil")>0?(int)Math.Clamp(Math.Ceiling(resources["native:oil"]*50),1,100):0,
                OwnerColor=colors.GetValueOrDefault(tile["owner"]?.ToString()??"","") };
            var counts=new Dictionary<string,int>();
            if(tile["building_instances"] is JsonArray instances)
            {
                foreach(var b in instances)
                {
                    string kind=b!["kind"]!.ToString();
                    r.Buildings.Add(new BuildingInput { Id=$"b/{b["id"]!.GetValue<int>():D10}",Kind=kind.EndsWith("farm")?"farm":kind.Contains("mine")?"mine":"house",Remaining=b["remaining"]!.GetValue<int>(),Art=kind });
                }
                bool town=instances.Any(b=>b!["kind"]!.ToString().EndsWith("town-hall",StringComparison.Ordinal));
                r.People=r.Passable&&tile["owner"]!=null?Math.Clamp((town?24:8)+instances.Count*4,8,town?64:36):0;
            }
            else foreach(var b in tile["buildings"]!.AsArray().OrderBy(b=>b![0]!.ToString(),StringComparer.Ordinal))
            {
                string kind=b![0]!.ToString();int index=counts.GetValueOrDefault(kind);counts[kind]=index+1;
                r.Buildings.Add(new BuildingInput { Id=$"{kind}/{index}",Kind=kind.EndsWith("farm")?"farm":kind.Contains("mine")?"mine":"house",Art=kind });
            }
            input.Regions.Add(r);
        }
        return input;
    }
}
