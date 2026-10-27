using System;
using System.Linq;
using System.Text.Json.Nodes;
using Zhanguo.Desktop.Layout;

static class SquadChecks
{
    static void Require(bool ok,string message){if(!ok)throw new Exception(message);}
    public static void Run()
    {
        var frame=JsonNode.Parse("""
        {"names":{"qin":"秦","chu":"楚"},"tiles":[{"position":[2,3],"owner":"qin"}],
         "scene":{"unit_types":[{"id":"native:infantry","max_hp":100},{"id":"native:militia","max_hp":80}],
                  "tile_defenses":[{"id":"2,3","x":2,"y":3,"terrain":20,"castle":10}]},
         "armies":[{"owner":"qin","id":1,"kind":"native:infantry","hp":100,"x":2,"y":3,"engaged":false},
                   {"owner":"qin","id":2,"kind":"native:infantry","hp":80,"x":2,"y":3,"engaged":false},
                   {"owner":"chu","id":1,"kind":"native:militia","hp":60,"x":2,"y":3,"engaged":true}]}
        """)!.AsObject();
        var armies=frame["armies"]!.AsArray();var qin=armies.Take(2).Select(u=>u!).ToArray();
        var state=SquadView.Summarize(frame,qin);
        Require(state.Count==2&&state.Hp==180&&state.Maximum==200&&state.Health=="180/200"&&state.Strength=="步×2","squad figures leaked into rule strength or maximum HP");
        Require(state.Defense==28&&state.Terrain==20&&state.Fortress==10&&state.Defending,"land defense did not combine actual terrain and fortress reduction");
        var highFortress=frame.DeepClone().AsObject();
        highFortress["scene"]!["tile_defenses"]![0]!["terrain"]=25;highFortress["scene"]!["tile_defenses"]![0]!["castle"]=110;
        Require(SquadView.Summarize(highFortress,qin).Defense==108,"defense must preserve native floor division even above 100 percent");
        var attacker=SquadView.Summarize(frame,new[]{armies[2]!});
        Require(attacker.Maximum==80&&attacker.Defense==0&&!attacker.Defending,"militia max HP was guessed as 100, or attacker received defender terrain cover");
        var combat=new JsonObject { ["before"]=new JsonArray(qin.Select(u=>u.DeepClone()).ToArray()),["after"]=new JsonArray(new JsonObject { ["owner"]="qin",["id"]=1,["hp"]=70 }) };
        string unchanged=frame.ToJsonString();
        Require(SquadView.Summarize(frame,qin,combat,0).Hp==180&&SquadView.Summarize(frame,qin,combat,1).Hp==70,"battle bar did not reach actual before/after HP");
        double last=180;
        for(int i=0;i<=100;i++){double hp=SquadView.Summarize(frame,qin,combat,i/100f).Hp;Require(hp<=last+.001&&hp>=70-.001,"battle invented extra damage or healing");last=hp;}
        Require(frame.ToJsonString()==unchanged,"visual damage changed observer facts");
        var wild=JsonNode.Parse("""{"owner":"野人","id":51,"kind":"native:infantry","hp":100,"x":2,"y":3}""")!;
        var shared=frame.DeepClone().AsObject();
        shared["scene"]!["guards"]=new JsonArray(wild.DeepClone());
        var lazy=SquadView.Units(shared).Single(u=>u["owner"]!.ToString()=="野人");
        shared["armies"]!.AsArray().Add(wild.DeepClone());shared["armies"]!.AsArray().Last()!["hp"]=65;
        var activeWild=SquadView.Units(shared).Where(u=>u["owner"]!.ToString()=="野人").ToArray();
        Require(activeWild.Length==1&&activeWild[0]["hp"]!.GetValue<int>()==65,"idle guard duplicated or overrode the live battle unit");
        Require(DesktopScene.Key("armies",lazy)==DesktopScene.Key("armies",activeWild[0])&&DesktopScene.SquadFigures(1)==DesktopScene.SquadFigures(activeWild.Length),"wild garrison identity or formation changed at battle activation");
        shared["armies"]!.AsArray().Last()!["hp"]=0;
        Require(!SquadView.Units(shared).Any(u=>u["owner"]!.ToString()=="野人"),"dead battle unit revived through a stale idle guard");
        var panels=new[]{new Footprint(16,78,280,278),new Footprint(504,216,504,468)};
        var between=ResourceOverlayLayout.Place(new Footprint(100,286,176,64),panels,1024,768,230,72);
        Require(!panels.Any(p=>p.Overlaps(between,3)),"army HP bar hid behind the country ledger or drawer in a narrow viewport");
        Require(PlaybackPolicy.Speed(16,"rule","actor")==16&&PlaybackPolicy.Speed(16,"llm","settlement")==16,"rule or settlement lost fast-forward");
        Require(PlaybackPolicy.Speed(16,"human","actor")==16&&PlaybackPolicy.Speed(.5f,"human","actor")==.5f,"human operations jumped to 1x instead of the selected speed");
        Require(PlaybackPolicy.Speed(16,"llm","actor")==1&&PlaybackPolicy.Speed(4,"sim","actor")==1&&PlaybackPolicy.Speed(96,null,"actor")==1,"non-rule actor was fast-forwarded");
        var popups=new System.Collections.Generic.List<Footprint>();
        foreach(float width in new[]{72f,114,85,130,60,100})
        {
            var placed=ResourceOverlayLayout.Place(new Footprint(620,400,width,23),popups,1440,900,230);
            Require(!popups.Any(p=>p.Overlaps(placed,2)),"resource text overlaps at one building");popups.Add(placed);
        }
        Console.WriteLine("SQUAD_HUD_OK: actual HP/max/count, defending terrain, militia, staged loss, immutable facts and LLM speed policy");
    }
}
