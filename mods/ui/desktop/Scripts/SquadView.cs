using System;
using System.Collections.Generic;
using System.Linq;
using System.Text.Json.Nodes;

namespace Zhanguo.Desktop.Layout;

// Read-only view of a rule squad. Representative sprite figures never supply
// its unit count, HP maximum or terrain/fortress defense.
public static class SquadView
{
    public sealed record Status(string Owner,string Name,string Kind,int Count,double Hp,double Maximum,
        int Terrain,int Fortress,int Defense,bool Defending)
    {
        public bool DefenseKnown {get;init;}=true;
        public string DefenseText=>DefenseKnown?$"{Defense}% 地块防御":"地块防御未确认";
        public float Fraction=>Maximum<=0?0:(float)Math.Clamp(Hp/Maximum,0,1);
        public string Health=>$"{Hp:G4}/{(Maximum>0?Maximum.ToString("G4",System.Globalization.CultureInfo.InvariantCulture):"—")}";
        public string Strength=>(Kind.EndsWith("cavalry",StringComparison.Ordinal)?"骑兵":Kind.EndsWith("militia",StringComparison.Ordinal)?"民兵":Kind.EndsWith("infantry",StringComparison.Ordinal)?"步":DesktopScene.UnitName(Kind))+"×"+Count;
    }
    private static int I(JsonNode? node)=>int.TryParse(node?.ToString(),out int value)?value:0;
    private static double F(JsonNode? node)=>double.TryParse(node?.ToString(),System.Globalization.NumberStyles.Float,System.Globalization.CultureInfo.InvariantCulture,out double value)?value:0;
    // A lazy wild garrison and its materialized battle unit retain the same
    // owner/id. Prefer the staged army record; never draw both representations.
    public static IEnumerable<JsonNode> Units(JsonObject frame)=>(frame["armies"]?.AsArray()??new JsonArray())
        .Concat(frame["scene"]?["guards"]?.AsArray()??new JsonArray()).Where(u=>u!=null)
        .GroupBy(u=>DesktopScene.Key("armies",u!)).Select(g=>g.First()!).Where(u=>F(u["hp"])>0);
    public static Status Summarize(JsonObject frame,IEnumerable<JsonNode> units,JsonObject? combat=null,float progress=0)
    {
        var rows=units.ToArray();var first=rows.First();
        string owner=first["owner"]!.ToString(),kind=first["kind"]!.ToString();
        int x=I(first["x"]),y=I(first["y"]);
        var tile=frame["tiles"]?.AsArray().FirstOrDefault(t=>I(t?["position"]?[0])==x&&I(t?["position"]?[1])==y);
        var defense=frame["scene"]?["tile_defenses"]?.AsArray().FirstOrDefault(t=>I(t?["x"])==x&&I(t?["y"])==y);
        int terrain=I(defense?["terrain"]),castle=owner==tile?["owner"]?.ToString()?I(defense?["castle"]):0;
        bool active=frame["armies"]?.AsArray().Any(u=>u?["owner"]?.ToString()==owner&&I(u["x"])==x&&I(u["y"])==y&&u["engaged"]?.GetValue<bool>()==true)==true;
        bool defending=owner==tile?["owner"]?.ToString()||owner=="野人"||!active;
        int cover=defending?100-(int)Math.Floor((100.0-terrain)*(100-castle)/100):0;
        var maxima=frame["scene"]?["unit_types"]?.AsArray().ToDictionary(t=>t!["id"]!.ToString(),t=>F(t!["max_hp"]));
        double hp=0,max=0;
        foreach(var row in rows)
        {
            double current=F(row["hp"]);
            if(combat?["before"] is JsonArray before&&combat["after"] is JsonArray after)
            {
                string key=DesktopScene.Key("armies",row);
                var old=before.FirstOrDefault(u=>DesktopScene.Key("armies",u!)==key);
                if(old!=null)
                {
                    var result=after.FirstOrDefault(u=>DesktopScene.Key("armies",u!)==key);
                    current=F(old["hp"])+(F(result?["hp"])-F(old["hp"]))*DamageProgress(progress);
                }
            }
            hp+=Math.Max(0,current);
            double cap=F(row["max_hp"]);max+=cap>0?cap:maxima?.GetValueOrDefault(row["kind"]!.ToString())??0;
        }
        return new Status(owner,frame["names"]?[owner]?.ToString()??owner,kind,rows.Length,hp,max,terrain,castle,cover,defending) { DefenseKnown=defense?["castle_known"]?.GetValue<bool>()!=false||!defending||owner!=tile?["owner"]?.ToString() };
    }
    // One real resolution is choreographed as three exchanges. These timings
    // interpolate the known before/after HP; they do not roll extra damage.
    public static float DamageProgress(float progress)
    {
        float t=Math.Clamp(progress,0,1),amount=0;
        foreach(float hit in new[]{.29f,.55f,.81f})amount+=Math.Clamp((t-hit)/.055f,0,1)/3;
        return Math.Clamp(amount,0,1);
    }
    public static float Strike(float progress)
    {
        float t=Math.Clamp(progress,0,1),reach=0;
        foreach(float hit in new[]{.29f,.55f,.81f})
        {
            float phase=(t-(hit-.08f))/.16f;
            if(phase is >0 and <1)reach=Math.Max(reach,MathF.Sin(phase*MathF.PI));
        }
        return reach;
    }
    public static float AttackPhase(float progress)
    {
        foreach(float hit in new[]{.29f,.55f,.81f})
        {
            float phase=(progress-(hit-.08f))/.16f;
            if(phase is >=0 and <=1)return phase;
        }
        return 0;
    }
}
