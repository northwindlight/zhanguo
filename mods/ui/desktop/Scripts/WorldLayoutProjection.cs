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
                Gold=(int)Math.Clamp(resources.GetValueOrDefault("native:gold")*50,0,100) };
            var counts=new Dictionary<string,int>();
            foreach(var b in tile["buildings"]!.AsArray().OrderBy(b=>b![0]!.ToString(),StringComparer.Ordinal))
            {
                string kind=b![0]!.ToString();int index=counts.GetValueOrDefault(kind);counts[kind]=index+1;
                r.Buildings.Add(new BuildingInput { Id=$"{kind}/{index}",Kind=kind.EndsWith("farm")?"farm":kind.Contains("mine")?"mine":"house" });
            }
            input.Regions.Add(r);
        }
        return input;
    }
}
