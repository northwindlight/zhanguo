using Godot;
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Text.Json;
using System.Text.Json.Nodes;
using System.Threading.Tasks;
using Zhanguo.Desktop.Layout;
using static WorldMap;

public partial class Desktop
{
    private async Task ReviewEffects(string path)
    {
        try
        {
            _status.Text="独立离线演出验收 · 不调用模型";
            Directory.CreateDirectory(Path.GetDirectoryName(Path.GetFullPath(path))!);
            string fixture=Path.Combine(Path.GetDirectoryName(Path.GetFullPath(path))!,"effects-fixture-"+Guid.NewGuid().ToString("N")+".json");
            var start=new ProcessStartInfo(_runtime.Python) { WorkingDirectory=_runtime.ContentRoot,UseShellExecute=false,
                RedirectStandardOutput=true,RedirectStandardError=true,CreateNoWindow=true };
            foreach(string name in new[]{"PYTHONHOME","PYTHONPATH","ZHANGUO_DESKTOP_CONFIG","ZHANGUO_RESUME"})start.Environment.Remove(name);
            start.Environment["PYTHONIOENCODING"]="utf-8";
            foreach(string arg in new[]{"-X","utf8","-u","-m","mods.ui.desktop_review","--output",fixture})start.ArgumentList.Add(arg);
            using(var process=Process.Start(start)??throw new Exception("无法创建离线素材样例"))
            {
                var output=process.StandardOutput.ReadToEndAsync();var error=process.StandardError.ReadToEndAsync();
                await process.WaitForExitAsync();if(process.ExitCode!=0)throw new Exception(await error);GD.Print(await output);
            }
            var input=JsonNode.Parse(File.ReadAllText(fixture))!;var frame=input["frame"]!.DeepClone().AsObject();
            VerifyUnifiedArt();var shots=new HashSet<string>();var checks=new List<object>();string stem=Path.ChangeExtension(path,null);
            foreach(var row in input["beats"]!.AsArray())
            {
                var beat=row!.AsObject();DesktopScene.Apply(frame,beat["delta"]!.AsObject());string phase=S(beat["phase"]);
                if(!new[]{"trade","order","production","maintenance","battle","letter"}.Contains(phase)||shots.Contains(phase))continue;
                if(phase=="order"&&(beat["approaches"]?.AsArray().Count??0)==0)continue;
                ApplyFrame(frame.DeepClone().AsObject());_timelinePeriod=I(beat["ledger"]?["period"]??input["period"]);
                _map.BeginBeat(beat,true);ShowNationalSettlement(beat);_map.BeatProgress(.48f);
                _bannerTitle.Text=S(beat["turn_title"]);_bannerText.Text=S(beat["title"])+"\n"+S(beat["text"]);
                await ToSignal(GetTree().CreateTimer(1),SceneTreeTimer.SignalName.Timeout);
                ValidateLedgerLayout();
                if(phase is "order" or "battle"&&_map.VisibleWarArrows==0)throw new Exception("战争方向箭头未显示");
                if(phase=="letter"&&_map.VisibleLetters!=1)throw new Exception("实际送达信件未显示传递动画");
                if(phase=="trade"&&_map.MarketCount==0)throw new Exception("交易没有集市场所");
                await Screenshot(stem+"-"+phase+".png");shots.Add(phase);
                checks.Add(new { phase,markets=_map.MarketCount,roads=_map.RoadTiles,arrows=_map.VisibleWarArrows,letters=_map.VisibleLetters });
            }
            foreach(string required in new[]{"trade","order","production","maintenance","battle","letter"})
                if(!shots.Contains(required))throw new Exception("样例缺少实际演出："+required);
            _map.EndBeat();_settlementCard.Visible=false;_bubbles.HideAll();
            foreach(float zoom in new[]{.85f,.95f,2f})
            {
                _map.PreviewBirds(zoom);await ToSignal(GetTree().CreateTimer(.4),SceneTreeTimer.SignalName.Timeout);
                if(_map.VisibleBirds<6)throw new Exception("鸟群在缩放时消失："+zoom);
                await Screenshot(stem+$"-birds-{zoom:0.00}.png");checks.Add(new { zoom,birds=_map.VisibleBirds });
            }
            File.WriteAllText(path+".json",JsonSerializer.Serialize(new { offline=true,normal_start=true,subsidies=false,national_colors_checked=true,checks },new JsonSerializerOptions { WriteIndented=true }));
            GD.Print("EFFECTS_REVIEW_OK "+path);GetTree().Quit();
        }
        catch(Exception e){GD.PrintErr(e);GetTree().Quit(3);}
    }
}
