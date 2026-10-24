using Godot;
using System;
using System.IO;
using System.Linq;
using System.Text.Json;
using System.Text.Json.Nodes;
using System.Threading.Tasks;
using Zhanguo.Desktop.Layout;

public partial class Desktop
{
    private async Task ReviewActors(string path)
    {
        try
        {
            Directory.CreateDirectory(Path.GetDirectoryName(path)!);
            var input=OS.GetEnvironment("ZHANGUO_ACTOR_REVIEW_INPUT");
            var data=JsonNode.Parse(File.ReadAllText(input))!.AsObject();
            string stem=Path.ChangeExtension(path,null);
            _bubbles.HideAll();_status.Text="独立离线士兵样例 · 非默认开局";
            _map.SetFrame(data["camp"]!.DeepClone().AsObject());
            _bannerTitle.Text="军队状态与动作检查";_bannerText.Text="按真实部队数量、兵种上限和地块防御显示。";
            _map.BeginBeat(new JsonObject { ["phase"]="recruit",["position"]=data["camp_position"]!.DeepClone(),["duration"]=1,["delta"]=new JsonObject() },true);
            _map.BeatProgress(1);
            await ToSignal(GetTree().CreateTimer(1.4),SceneTreeTimer.SignalName.Timeout);
            if(!_map.LastSquadLabels.Any(s=>s.Contains("秦")&&s.Contains("200/200")&&s.Contains("步×2"))||
               !_map.LastSquadLabels.Any(s=>s.Contains("秦")&&s.Contains("200/200")&&s.Contains("骑兵×2")))throw new Exception("军队血量或脚下兵种数量未显示");
            var cards=_map.SquadLabelBounds.ToArray();
            for(int i=0;i<cards.Length;i++)for(int j=i+1;j<cards.Length;j++)
                if((cards[i] with { Height=cards[i].Height-35 }).Overlaps(cards[j] with { Height=cards[j].Height-35 }))throw new Exception("军队血条卡片重叠");
            await Screenshot(path);
            var campLabels=_map.LastSquadLabels.ToArray();

            var hall=_map.Frame!["scene"]!["buildings"]!.AsArray().First(b=>b!["kind"]!.ToString()=="native:town-hall"&&b["owner"]!.ToString()=="scenario:qin")!;
            _bannerTitle.Text="市政厅与城镇聚落";_bannerText.Text="更大的市政厅、国旗、院落与住宅街区。住宅是场景装饰。";
            _map.BeginBeat(new JsonObject { ["phase"]="complete",["source"]="building/"+hall["id"],
                ["position"]=new JsonArray(hall["x"]!.GetValue<int>(),hall["y"]!.GetValue<int>()),["duration"]=1,["delta"]=new JsonObject() },true);
            _map.BeatProgress(1);
            await ToSignal(GetTree().CreateTimer(1.4),SceneTreeTimer.SignalName.Timeout);
            if(_map.TownSceneryCount==0||_map.TownHallCount==0)throw new Exception("市政厅城镇装饰未显示");
            await Screenshot(stem+"-town.png");
            var oil=_map.Frame!["tiles"]!.AsArray().First(t=>t!["passable"]?.GetValue<bool>()==true&&t["resources"]!.AsArray().Any(r=>r![0]!.ToString()=="native:oil"&&WorldMap.F(r[1])>0));
            _bannerTitle.Text="油区地表 · 沥青渗出";_bannerText.Text="深色渗油斑来自当前地块的实际石油资源丰度。";
            _map.BeginBeat(new JsonObject { ["phase"]="production",["position"]=oil!["position"]!.DeepClone(),["duration"]=1,["delta"]=new JsonObject() },true);
            _map.BeatProgress(1);
            await ToSignal(GetTree().CreateTimer(1.4),SceneTreeTimer.SignalName.Timeout);
            if(_map.OilPatchCount==0)throw new Exception("真实油区没有地表沥青");
            await Screenshot(stem+"-oil.png");

            // Real controller timing: a selected 16x is suspended for LLM,
            // then automatically available for rule actors and settlement.
            _playbackSpeed=16;_speedControl.Select(4);_playbackPaused=false;
            foreach(var kind in new[]{"llm","rule"})
            {
                _playingBeat=new JsonObject { ["client_kind"]=kind,["playback_scope"]="actor",["duration"]=100,["phase"]="action" };
                _beatElapsed=0;RefreshSpeedDisplay();ProcessPlayback(.2);
                if(Math.Abs(_beatElapsed-(kind=="llm"?.2:3.2))>.001)throw new Exception("LLM/规则AI速度切换错误");
                if(kind=="llm"&&_speedControl.Text!="LLM 1×")throw new Exception("LLM正常速度状态未显示");
            }
            _playingBeat=new JsonObject { ["client_kind"]="llm",["playback_scope"]="settlement",["duration"]=100,["phase"]="production" };
            _beatElapsed=0;ProcessPlayback(.2);if(Math.Abs(_beatElapsed-3.2)>.001)throw new Exception("回合结算未恢复快进");
            _playingBeat=null;_playbackPaused=true;_playbackSpeed=1;_speedControl.Select(1);RefreshSpeedDisplay();

            _bannerTitle.Text="同位置资源显示检查";_bannerText.Text="模拟资源标签用于核对布局；相同位置也应分开显示。";
            var pops=new JsonArray();foreach(var kind in new[]{"native:gold","native:ore","native:oil","native:wood"})
                pops.Add(new JsonObject { ["position"]=data["camp_position"]!.DeepClone(),["resource"]=kind,["amount"]=kind=="native:gold"?120:kind=="native:ore"?-23:12 });
            _map.BeginBeat(new JsonObject { ["phase"]="production",["position"]=data["camp_position"]!.DeepClone(),["duration"]=2,["delta"]=new JsonObject(),["pops"]=pops },true);
            _map.BeatProgress(.25f);
            await ToSignal(GetTree().CreateTimer(.5),SceneTreeTimer.SignalName.Timeout);
            var boxes=_map.ResourcePopBounds.ToArray();
            if(boxes.Length!=4||boxes.SelectMany((b,i)=>boxes.Skip(i+1).Select(c=>(b,c))).Any(p=>p.b.Overlaps(p.c,1)))throw new Exception("黄金、矿石等资源标签仍有重叠");
            await Screenshot(stem+"-resources.png");

            _map.SetFrame(data["battle"]!.DeepClone().AsObject());
            var battle=data["beat"]!.DeepClone().AsObject();_map.BeginBeat(battle,true);
            _bannerTitle.Text="接战 · 真实规则伤害的动作分解";
            foreach(float progress in new[]{0f,.28f,.36f,.55f,.62f,.81f,.90f,1f})
            {
                _map.BeatProgress(progress);_bannerText.Text=progress<.2?"前压接敌":progress<.88?"挥击、格挡与受击；血量逐段抵达本次真实结果":"收阵；等待伤亡确认";
                await ToSignal(GetTree().CreateTimer(.3),SceneTreeTimer.SignalName.Timeout);
                await Screenshot(stem+$"-battle-{(int)(progress*100):D3}.png");
            }
            var after=data["beat"]!["combat"]!["after"]!.AsArray();
            foreach(var group in after.GroupBy(u=>(Owner:u!["owner"]!.ToString(),Kind:u["kind"]!.ToString())))
            {
                var before=data["beat"]!["combat"]!["before"]!.AsArray().Where(u=>u!["owner"]!.ToString()==group.Key.Owner&&u["kind"]!.ToString()==group.Key.Kind).Select(u=>u!);
                var status=SquadView.Summarize(_map.Frame!,before,battle["combat"]!.AsObject(),1);
                if(Math.Abs(status.Hp-group.Sum(u=>WorldMap.F(u!["hp"])))>.001)throw new Exception("血条未抵达本次战斗真实HP");
            }
            _map.BeginBeat(data["casualties"]!.DeepClone().AsObject(),true);_map.BeatProgress(.35f);
            await ToSignal(GetTree().CreateTimer(.6),SceneTreeTimer.SignalName.Timeout);
            await Screenshot(stem+"-casualties.png");
            File.WriteAllText(path+".json",JsonSerializer.Serialize(new { camp_labels=campLabels,resource_labels=boxes.Length,resource_overlap=false,rule_speed=16,llm_speed=1,settlement_speed=16,actual_battle_hp=true,figures=_map.VisibleSoldiers,bars=_map.VisibleSquadBars,town_halls=_map.TownHallCount,town_scenery=_map.TownSceneryCount,oil_patches=_map.OilPatchCount },new JsonSerializerOptions { WriteIndented=true }));
            GD.Print("ACTOR_REVIEW_OK "+path);GetTree().Quit();
        }
        catch(Exception e){_playingBeat=null;_playbackPaused=true;GD.PrintErr(e);GetTree().Quit(3);}
    }
}
