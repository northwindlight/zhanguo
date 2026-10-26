using System;
using System.Collections.Generic;
using System.Linq;
using System.Text.Json.Nodes;

namespace Zhanguo.Desktop.Layout;

// Pure presentation state: apply ordered facts, then update map and panels
// together. No renderer or game rule is needed to replay these deltas.
public static class DesktopScene
{
    public static readonly string[] NationPalette={"9eb5cf","e5a47c","c69ddd","91bd98","e8cc83","76bdbb","d68c95","ddd9af","c7b29e","a8a7d9"};
    public static string Key(string collection,JsonNode row) => collection switch {
        "armies" or "guards"=>$"{row["owner"]}/{row["id"]}",
        "ownership"=>$"{row["x"]},{row["y"]}",_=>row["id"]!.ToString() };

    public static void Apply(JsonObject frame,JsonObject delta)
    {
        var scene=frame["scene"]!.AsObject();
        foreach(var (collection,value) in delta)
        {
            var change=value!.AsObject();
            var rows=(scene[collection]?.AsArray()??new JsonArray()).ToDictionary(r=>Key(collection,r!),r=>r!);
            foreach(var id in change["remove"]!.AsArray())rows.Remove(id!.ToString());
            foreach(var row in change["upsert"]!.AsArray())rows[Key(collection,row!)]=row!;
            scene[collection]=new JsonArray(rows.Values.Select(r=>r.DeepClone()).ToArray());
        }
        Synchronize(frame);
    }

    public static void Synchronize(JsonObject frame)
    {
        if(frame["scene"] is not JsonObject scene || scene["buildings"] is not JsonArray buildings)return;
        var owners=scene["ownership"]!.AsArray().ToDictionary(r=>(I(r!["x"]),I(r["y"])),r=>r!["owner"]);
        var byTile=buildings.ToLookup(b=>(I(b!["x"]),I(b["y"])));
        foreach(var t in frame["tiles"]!.AsArray())
        {
            if(t?["visibility"]?.ToString()=="unknown")continue;
            var at=(I(t!["position"]![0]),I(t["position"]![1]));
            t["owner"]=owners.GetValueOrDefault(at)?.DeepClone();
            var local=byTile[at].OrderBy(b=>I(b!["id"])).ToArray();
            t["building_instances"]=new JsonArray(local.Select(b=>b!.DeepClone()).ToArray());
            t["buildings"]=new JsonArray(local.Select(b=>(JsonNode)new JsonArray(b!["kind"]!.ToString(),I(b["level"]))).ToArray());
            t["building_labels"]=new JsonArray(local.Select(b=>JsonValue.Create($"{b!["label"]??b["kind"]}{(b["level_known"]?.GetValue<bool>()==false?$"（示意 · 第{I(b["sampled"])+1}期）":$" Lv.{b["level"]}")}{(I(b["remaining"])>0?" · 施工中":"")}")).Cast<JsonNode?>().ToArray());
        }
        frame["armies"]=scene["armies"]!.DeepClone();
        var accounts=scene["accounts"]?.AsArray().ToDictionary(a=>a!["id"]!.ToString());
        var flows=scene["resource_flows"]?.AsArray().ToDictionary(a=>a!["id"]!.ToString());
        foreach(var n in frame["nations"]?.AsArray()??new JsonArray())
        {
            string id=n!["id"]!.ToString();
            if(accounts?.TryGetValue(id,out var account)==true)n["stocks"]=account!["stocks"]!.DeepClone();
            if(flows?.TryGetValue(id,out var flow)==true)n["resource_flow"]=flow!.DeepClone();
            n["land"]=owners.Values.Count(v=>v?.ToString()==id);
            var units=scene["armies"]!.AsArray().Where(u=>u!["owner"]!.ToString()==id&&F(u["hp"])>0);
            var counts=new JsonObject();foreach(var g in units.GroupBy(u=>UnitName(u!["kind"]!.ToString())))counts[g.Key]=g.Count();
            n["units"]=counts;
        }
        if(scene["market"] is JsonArray market)frame["market"]=market.DeepClone();
    }
    public static float NationOpacity(float zoom)
    {
        float t=Math.Clamp((zoom-.12f)/.70f,0,1);t=t*t*(3-2*t);
        return .68f*(1-t)+.10f*t;
    }
    public static int SquadFigures(int units)=>Math.Clamp(units*8,8,48);
    public static string PriceText(double? value)=>value is double price?price.ToString("G5",System.Globalization.CultureInfo.InvariantCulture):"无货";
    public static string UnitName(string kind)=>kind switch { "native:cavalry"=>"骑兵","native:infantry"=>"步兵","native:militia"=>"民兵",_=>kind };
    private static int I(JsonNode? n)=>n?.GetValue<int>()??0;
    private static double F(JsonNode? n)=>double.TryParse(n?.ToString(),System.Globalization.NumberStyles.Float,System.Globalization.CultureInfo.InvariantCulture,out double value)?value:0;
}
