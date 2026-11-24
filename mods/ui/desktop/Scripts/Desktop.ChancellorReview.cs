using Godot;
using System;
using System.IO;
using System.Linq;
using System.Text.Json;
using System.Text.Json.Nodes;
using System.Threading.Tasks;
using static WorldMap;

public partial class Desktop
{
    /// <summary>
    /// 丞相离线验收：脚本化模型（ZHANGUO_CHANCELLOR_SCRIPT）拟奏 → 呈上奏折 → 准一条、驳一条 →
    /// 下口谕 → 立方针；核对回执回写与布局，并截取多种窗口尺寸。
    /// </summary>
    private async Task ReviewChancellor(string path)
    {
        _humanReviewDiagnostics=true;
        string stem=Path.ChangeExtension(Path.GetFullPath(path),null);
        try
        {
            if(OS.GetEnvironment("ZHANGUO_CHANCELLOR_SCRIPT")=="")throw new Exception("丞相验收需要 ZHANGUO_CHANCELLOR_SCRIPT 脚本");
            FocusWindowForPointer();
            string reviewRoot=stem+"-data";Directory.CreateDirectory(reviewRoot);
            _runtime=_runtime with { DataRoot=reviewRoot };
            _menu=new MainMenu(reviewRoot,MenuBegin);_menu.SetAnchorsAndOffsetsPreset(LayoutPreset.FullRect);_menu.ZIndex=30;AddChild(_menu);
            _menu.OpenPlayerGame();_menu.PlayerSeedField.Text="20261026";_menu.PlayerNameField.Text="青岚";
            _menu.ReviewChancellorProfile();
            await ToSignal(GetTree().CreateTimer(.3),SceneTreeTimer.SignalName.Timeout);
            if(_menu.ChancellorProfile==null)throw new Exception("菜单没有选中丞相档案");
            _menu.ValidateVisibleLayout();await Screenshot(stem+"-menu.png");
            _menu.SelectPlayerScenario(0);await PointerClick(_menu.PlayerNextButton);
            _menu.ReviewRuleSeeds(20261026);
            var config=_menu.BuildConfig(out string error,out string? configPath);
            if(config==null||configPath==null)throw new Exception("真人菜单配置失败："+error);
            if(config.ToJsonString().Contains("api_key")&&config["nations"]!.AsArray().All(n=>S(n!["client"])!="llm"))
                throw new Exception("丞相档案不应写入游戏配置");
            _menu.Visible=false;StartHost(configPath,null);
            await HumanIdle();
            await WaitFor(()=>_chancellorEnabled&&_chancellorPeriod>=0&&!_chancellorBusy,"丞相没有呈上首回合奏折",120);
            if(_chancellorNavButton==null||!_chancellorNavButton.Visible)throw new Exception("设了丞相但底栏没有入口");
            if(!_humanDrawer.IsOpen||_humanPage!="chancellor")throw new Exception("奏折到达时没有自动呈上");
            int items=_chancellorItems.Count,dropped=_chancellorDropped.Count;
            if(items<2)throw new Exception("奏折条目不足，无法验收准驳："+items);
            await ToSignal(GetTree().CreateTimer(.3),SceneTreeTimer.SignalName.Timeout);
            ValidateHumanLayout();await Screenshot(stem+"-memorial.png");

            var first=_chancellorItems.First(i=>i.Calls.Count>0&&!i.Major);
            await PointerClick((Button)_humanContent.FindChild("ChancellorApprove_"+first.Id,true,false)!);
            await WaitFor(()=>first.Status is "done" or "failed","准奏的操作没有回执",120);await HumanIdle();
            if(first.Status!="done")throw new Exception("准奏操作未成："+first.Note);
            var second=_chancellorItems.First(i=>i.Status=="pending");
            await PointerClick((Button)_humanContent.FindChild("ChancellorReject_"+second.Id,true,false)!);
            if(second.Status!="rejected")throw new Exception("驳回没有生效");
            if(_humanHistory.Count==0)throw new Exception("准奏操作没有进入本次会话操作记录");
            await ToSignal(GetTree().CreateTimer(.3),SceneTreeTimer.SignalName.Timeout);
            ValidateHumanLayout();await Screenshot(stem+"-approved.png");

            var edict=(LineEdit)_humanContent.FindChild("ChancellorEdict",true,false)!;edict.Text="粮食保持一百以上";
            await PointerClick((Button)_humanContent.FindChild("ChancellorSendEdict",true,false)!);
            await WaitFor(()=>!_chancellorBusy&&_chancellorReply!="","口谕没有得到回应",120);
            await ToSignal(GetTree().CreateTimer(.3),SceneTreeTimer.SignalName.Timeout);
            ValidateHumanLayout();await Screenshot(stem+"-edict.png");

            var directive=(LineEdit)_humanContent.FindChild("ChancellorDirective",true,false)!;directive.Text="金币低于三百不再扩军";
            await PointerClick((Button)_humanContent.FindChild("ChancellorAddDirective",true,false)!);
            await WaitFor(()=>_chancellorDirectives.Contains("金币低于三百不再扩军"),"方针没有立住",30);
            await ToSignal(GetTree().CreateTimer(.3),SceneTreeTimer.SignalName.Timeout);
            ValidateHumanLayout();await Screenshot(stem+"-directive.png");

            var layouts=new JsonArray();
            foreach(var size in new[]{new Vector2I(1280,720),new Vector2I(1920,1080),new Vector2I(1024,768)})
            {
                GetWindow().ContentScaleSize=size;GetWindow().Size=size;OpenHumanPage("chancellor");
                await ToSignal(GetTree().CreateTimer(.3),SceneTreeTimer.SignalName.Timeout);
                ValidateHumanLayout();await Screenshot(stem+$"-{size.X}.png");layouts.Add(size.X);
            }
            File.WriteAllText(path+".json",new JsonObject {
                ["memorial_items"]=items,["dropped"]=dropped,["approved_done"]=first.Status=="done",["rejected"]=second.Status=="rejected",
                ["edict_reply"]=_chancellorReply,["directives"]=new JsonArray(_chancellorDirectives.Select(d=>(JsonNode?)d).ToArray()),
                ["memorial_seconds"]=_chancellorSeconds,["layouts"]=layouts }.ToJsonString(new JsonSerializerOptions { WriteIndented=true }));
            await Screenshot(path);
            GD.Print("CHANCELLOR_GUI_OK "+path);
            SendHumanRow(new JsonObject { ["command"]="disconnect" });_closing=true;
            await WaitFor(()=>_stopped,"宿主没有退出",60);GetTree().Quit();
        }
        catch(Exception e)
        {
            GD.PrintErr(e);
            try{await Screenshot(stem+"-failed.png");}catch{}
            if(_host!=null){SendHumanRow(new JsonObject { ["command"]="disconnect" });_closing=true;}
            GetTree().Quit(3);
        }
    }
}
