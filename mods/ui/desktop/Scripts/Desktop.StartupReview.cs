using Godot;
using System;
using System.IO;
using System.IO.Compression;
using System.Linq;
using System.Text.Json.Nodes;
using System.Threading.Tasks;

public partial class Desktop
{
    // Archived synthetic saves are test inputs, never files from a real game.
    // Windows supplies them from the checkout; all checks execute bundled code.
    private T StartupControl<T>(string name) where T:Control
    {
        T? Find(Node parent)
        {
            foreach(var child in parent.GetChildren())
            {
                if(child is T control&&control.Name==name&&control.IsVisibleInTree())return control;
                if(Find(child) is T found)return found;
            }
            return null;
        }
        return Find(_menu)??throw new Exception("启动验收缺少控件："+name);
    }

    private async Task ReviewStartup(string path)
    {
        try
        {
            string stem=Path.ChangeExtension(Path.GetFullPath(path),null);
            string root=stem+"-data-"+Guid.NewGuid().ToString("N")[..6];Directory.CreateDirectory(root);
            _runtime=_runtime with {DataRoot=root};
            int starts=0;string? resumed=null,startedConfig=null;
            _menu=new MainMenu(root,(config,save)=>{starts++;startedConfig=config;resumed=save;});_menu.ZIndex=30;AddChild(_menu);
            _menu.OpenNewGame();
            await ToSignal(GetTree(),SceneTree.SignalName.ProcessFrame);
            var native=_menu.BuildConfig(out string error,out _);
            if(native?["mods"]?.ToString()!="mods.toml"||native["experimental_features"]!=null)throw new Exception("原生版配置错误："+error);
            var picker=StartupControl<OptionButton>("EditionPicker");picker.Select(1);picker.EmitSignal(OptionButton.SignalName.ItemSelected,1L);
            await PointerClick(StartupControl<Button>("StartGame"));
            await WaitFor(()=>_menu.SetupMessage.Contains("parliament.reviewer"),"议会缺少模型未在开局前说明",30);
            if(starts!=0)throw new Exception("议会模型未就绪却已启动");
            await Screenshot(stem+"-council-required.png");
            foreach(string key in new[]{"council","scripts","combat_research"})
                await PointerClick(StartupControl<CheckButton>("Feature_"+key));
            var disabled=_menu.BuildConfig(out error,out _);
            if(disabled==null||disabled["experimental_features"]!.AsObject().Any(p=>p.Value!.GetValue<bool>()))throw new Exception("开关未进入配置："+error);
            foreach(var client in _menu.FindChildren("NationClient","OptionButton",true,false).Cast<OptionButton>().Where(c=>c.IsVisibleInTree()))
                for(int i=0;i<client.ItemCount;i++)if(client.GetItemId(i)==1)throw new Exception("未注册的简易客户端仍可选");
            var layouts=new JsonArray();
            foreach(var size in new[]{new Vector2I(1920,1080),new Vector2I(1366,768),new Vector2I(1280,720),new Vector2I(1024,768)})
            {
                GetWindow().ContentScaleSize=size;GetWindow().Size=size;
                await ToSignal(GetTree().CreateTimer(.2),SceneTreeTimer.SignalName.Timeout);
                _menu.ValidateVisibleLayout();await Screenshot(stem+$"-edition-{size.X}.png");
                layouts.Add(new JsonObject {["width"]=size.X,["height"]=size.Y});
            }
            var originals=new System.Collections.Generic.Dictionary<string,string>();
            string saves=Path.Combine(root,"archive","desktop");
            foreach(string edition in new[]{"native","experimental"})
            {
                string directory=Path.Combine(saves,"old-"+edition);Directory.CreateDirectory(directory);
                using var compressed=File.OpenRead(Path.Combine(OS.GetEnvironment("ZHANGUO_STARTUP_FIXTURES"),"v07-march-"+edition+".json.gz"));
                using var gzip=new GZipStream(compressed,CompressionMode.Decompress);using var reader=new StreamReader(gzip);
                string original=await reader.ReadToEndAsync();originals[edition]=original;File.WriteAllText(Path.Combine(directory,"session.json"),original);
                File.WriteAllText(Path.Combine(directory,"config.json"),new JsonObject {
                    ["mods"]=edition=="native"?"mods.toml":"mods.experimental.toml",["map_size"]=12,["seed"]=73,
                    ["nations"]=new JsonArray(new JsonObject {["id"]="scenario:human",["name"]="秦",["client"]="human",["nickname"]="本地玩家"},
                        new JsonObject {["id"]="scenario:other",["name"]="楚",["client"]="rule"}) }.ToJsonString());
                Directory.CreateDirectory(Path.Combine(directory,"session.json.audit"));File.WriteAllText(Path.Combine(directory,"session.json.audit","synthetic.txt"),"合成历史");
            }
            _menu.OpenLoad();
            await WaitFor(()=>_menu.FindChild("UpgradeSave_old-native",true,false) is Button b&&b.Visible,"已知原生旧档未给出升级入口",30);
            await WaitFor(()=>_menu.FindChild("UpgradeSave_old-experimental",true,false) is Button b&&b.Visible,"已知实验旧档未给出升级入口",30);
            _menu.ValidateVisibleLayout();await Screenshot(stem+"-old-saves.png");
            string? nativeCopy=null;
            foreach(string edition in new[]{"native","experimental"})
            {
                int before=Directory.GetDirectories(saves,"upgraded-*").Length;
                await WaitFor(()=>_menu.FindChild("UpgradeSave_old-"+edition,true,false) is Button b&&b.IsVisibleInTree()&&!b.Disabled,"存档列表刷新后未完成检查",30);
                await PointerClick(StartupControl<Button>("UpgradeSave_old-"+edition));
                await WaitFor(()=>Directory.GetDirectories(saves,"upgraded-*").Length==before+1&&_menu.SetupMessage.Contains("已创建升级副本"),"升级副本未完成",45);
                string copy=Directory.GetDirectories(saves,"upgraded-*").OrderByDescending(Directory.GetCreationTimeUtc).First();
                if(File.ReadAllText(Path.Combine(saves,"old-"+edition,"session.json"))!=originals[edition])throw new Exception("原存档被覆盖");
                if(File.ReadAllText(Path.Combine(copy,"session.json.audit","synthetic.txt"))!="合成历史")throw new Exception("归档历史未复制");
                var data=JsonNode.Parse(File.ReadAllText(Path.Combine(copy,"session.json")))!;
                if(data["modules"]!["native:army"]!["military"]!["march_orders"]!.AsArray().Count==0)throw new Exception("持续路线丢失");
                if(edition=="native")nativeCopy=copy;
                else if(!data["modules"]!["experimental:python"]!["scenario:human"]!["source"]!.ToString().Contains("保留旧脚本"))throw new Exception("旧脚本丢失");
                await WaitFor(()=>_menu.FindChild("ResumeSave_"+Path.GetFileName(copy),true,false) is Button b&&!b.Disabled,"升级副本未通过正式读回",30);
            }
            await WaitFor(()=>_menu.FindChild("ResumeSave_"+Path.GetFileName(nativeCopy!),true,false) is Button b&&!b.Disabled,"刷新后原生副本仍未校验",30);
            await PointerClick(StartupControl<Button>("ResumeSave_"+Path.GetFileName(nativeCopy!)));
            await WaitFor(()=>starts==1,"升级副本未通过继续入口",30);
            if(resumed!=Path.Combine(nativeCopy!,"session.json")||JsonNode.Parse(File.ReadAllText(startedConfig!))!["mods"]!.ToString()!="mods.toml")
                throw new Exception("继续存档混用了当前菜单的实验版");
            await Screenshot(stem+"-upgraded-saves.png");
            _menu.OpenPlayerGame();_menu.SelectPlayerScenario(1);_menu.PlayerSeedField.Text="73";_menu.PlayerMapSizeField.Value=12;
            await PointerClick(_menu.PlayerNextButton);_menu.StartRequested=MenuBegin;
            await PointerClick(StartupControl<Button>("StartGame"));await HumanIdle();
            OpenPauseMenu();
            if(!_gameRulesLabel.Visible||!_gameRulesLabel.Text.Contains("实验版")||_gameRulesLabel.Text.Contains(" 开"))throw new Exception("游戏内未显示实际关闭的实验规则");
            await Screenshot(path);
            _host!.StandardInput.WriteLine("{\"command\":\"disconnect\"}");
            await WaitFor(()=>_host.HasExited,"验收宿主未退出",30);
            File.WriteAllText(path+".json",new JsonObject {
                ["edition_selection"]=true,["real_feature_switches"]=true,["model_preflight"]=true,["registered_clients"]=true,
                ["save_copy_upgrade"]=true,["originals_unchanged"]=true,["audit_preserved"]=true,["routes_preserved"]=true,
                ["scripts_preserved"]=true,["resume_uses_save_rules"]=true,["actual_game_rules"]=true,["layouts"]=layouts }.ToJsonString());
            GD.Print("STARTUP_GUI_OK "+path);GetTree().Quit();
        }
        catch(Exception e){GD.PrintErr(e);GD.PrintErr(_menu?.SetupMessage);if(_host is {HasExited:false})_host.StandardInput.WriteLine("{\"command\":\"disconnect\"}");GetTree().Quit(3);}
    }
}
