using Godot;
using System;
using System.Collections.Generic;
using System.Linq;
using System.Text.Json.Nodes;
using System.Threading.Tasks;
using Zhanguo.Desktop.Layout;

public partial class MainMenu
{
    private readonly JsonArray _editions=JsonNode.Parse(Godot.FileAccess.GetFileAsString("res://distribution-catalog.json"))!["editions"]!.AsArray();
    private readonly List<OptionButton> _editionPickers=new();
    private readonly Dictionary<string,bool> _featureSelection=new();
    private readonly List<(CheckButton Button,string Key)> _featureChecks=new();
    private readonly List<Control> _experimentalPanels=new();
    private readonly List<OptionButton> _reviewerPickers=new();
    private readonly List<Label> _editionSummaries=new();
    private int _editionIndex,_reviewerProfile;
    private bool _starting;
    private DesktopRuntime SetupRuntime=>DesktopRuntime.Resolve(OS.GetExecutablePath(),ProjectSettings.GlobalizePath("res://"),
        !OS.HasFeature("editor"),OS.GetEnvironment("ZHANGUO_ROOT"),OS.GetEnvironment("ZHANGUO_PYTHON"),OS.GetName()=="Windows") with { DataRoot=System.IO.Path.GetDirectoryName(System.IO.Path.GetDirectoryName(_settingsPath)!)! };
    private JsonObject SelectedEdition=>_editions[_editionIndex]!.AsObject();
    private string EditionId=>SelectedEdition["id"]!.ToString();
    private bool ClientSupported(int client)=>SelectedEdition["clients"]!.AsArray().Any(c=>c!.ToString()==new[]{"rule","sim","llm","human"}[client]);
    private JsonObject SelectedFeatures()=>new(_featureSelection.OrderBy(p=>p.Key).Select(p=>new KeyValuePair<string,JsonNode?>(p.Key,JsonValue.Create(p.Value))));
    public void SelectEdition(int index){_editionIndex=index;_hint.Text="";RefreshEditionControls();}
    public void SelectFeature(string key,bool enabled){_featureSelection[key]=enabled;_hint.Text="";RefreshEditionControls();}
    public string SetupMessage=>_hint.Text;

    private void BuildEditionControls(VBoxContainer parent)
    {
        var row=new HBoxContainer();parent.AddChild(row);row.AddChild(FieldLabel("规则发行版"));
        var pick=new OptionButton { Name="EditionPicker",SizeFlagsHorizontal=SizeFlags.ExpandFill };row.AddChild(pick);
        foreach(var edition in _editions)pick.AddItem(edition!["name"]+" · "+edition["version"]);
        _editionPickers.Add(pick);pick.ItemSelected+=i=>SelectEdition((int)i);
        var experimental=new VBoxContainer();experimental.AddThemeConstantOverride("separation",4);parent.AddChild(experimental);_experimentalPanels.Add(experimental);
        var options=_editions.First(e=>e!["id"]!.ToString()=="experimental")!["features"]!.AsObject();
        foreach(var (key,node) in options)
        {
            if(!_featureSelection.ContainsKey(key))_featureSelection[key]=node!["default"]!.GetValue<bool>();
            var check=new CheckButton { Name="Feature_"+key,Text=node!["name"]!.ToString(),TooltipText=node["description"]!.ToString() };
            check.AddThemeFontSizeOverride("font_size",16);experimental.AddChild(check);_featureChecks.Add((check,key));
            check.Toggled+=on=>SelectFeature(key,on);
        }
        var reviewer=new OptionButton { Name="CouncilReviewer",TooltipText="议会启用时，规则AI使用所选档案审核；模型国家默认使用自己的模型。" };
        experimental.AddChild(reviewer);_reviewerPickers.Add(reviewer);reviewer.ItemSelected+=i=>{_reviewerProfile=(int)i;_hint.Text="";RefreshEditionControls();};
        var summary=new Label { Name="EditionSummary",AutowrapMode=TextServer.AutowrapMode.WordSmart,Modulate=new Color("b7c9ae") };
        summary.AddThemeFontSizeOverride("font_size",14);parent.AddChild(summary);_editionSummaries.Add(summary);
        RefreshEditionControls(false);
    }
    private void RefreshEditionControls(bool refreshClients=true)
    {
        foreach(var pick in _editionPickers)pick.Select(_editionIndex);
        foreach(var box in _experimentalPanels)box.Visible=EditionId=="experimental";
        foreach(var (button,key) in _featureChecks)button.SetPressedNoSignal(_featureSelection[key]);
        _reviewerProfile=Math.Clamp(_reviewerProfile,0,_profiles.Count);
        foreach(var pick in _reviewerPickers)
        {
            pick.Clear();pick.AddItem("议会审查模型：未指定（模型国家默认使用自身）");
            foreach(var profile in _profiles)pick.AddItem("规则AI议会："+profile.Name);
            pick.Select(_reviewerProfile);pick.Visible=_featureSelection.GetValueOrDefault("council");
        }
        string features=EditionId=="native"?"无实验服务":string.Join(" · ",SelectedEdition["features"]!.AsObject().Select(p=>p.Value!["name"]+(_featureSelection[p.Key]?" 开":" 关")));
        foreach(var summary in _editionSummaries)summary.Text=$"本局规则：{SelectedEdition["name"]} {SelectedEdition["version"]}\n{features}";
        if(refreshClients&&_nationList!=null)RestoreNationEditor(_nations.ToList());
    }
    private bool ApplyEdition(JsonObject config,ref string error)
    {
        string entry=SelectedEdition["entry"]!.ToString();
        if(config["mods"]!=null&&config["mods"]!.ToString()!=entry){error="附加JSON的发行版与上方选择不一致，请在发行版选项中切换。";return false;}
        config["mods"]=entry;
        if(config["scenario"]?.ToString() is {Length:>0} scenario&&!SelectedEdition["scenarios"]!.AsArray().Any(s=>s!.ToString()==scenario)){error="所选发行版没有这个剧本。";return false;}
        if(EditionId=="experimental")
        {
            var flags=SelectedFeatures();
            if(config["experimental_features"]!=null&&!JsonNode.DeepEquals(config["experimental_features"],flags)){error="附加JSON的实验开关与上方选择不一致。";return false;}
            config["experimental_features"]=flags;
            if(_reviewerProfile>0&&_featureSelection["council"])
            {
                var profile=_profiles[_reviewerProfile-1];
                var reviewer=new JsonObject { ["provider"]=profile.Provider,["base_url"]=profile.BaseUrl,["api_key"]=profile.ApiKey,["model"]=profile.Model };
                ParseObject(profile.ExtraJson,"议会模型档案",ref error,reviewer);
                if(config["parliament"]==null)config["parliament"]=new JsonObject();
                if(config["parliament"]?["reviewer"]!=null&&!JsonNode.DeepEquals(config["parliament"]!["reviewer"],reviewer)){error="附加JSON与议会模型档案选择不一致。";return false;}
                config["parliament"]!["reviewer"]=reviewer;
            }
        }
        else if(config["experimental_features"]!=null){error="原生版不支持实验开关，请切换到实验版。";return false;}
        foreach(var nation in config["nations"]!.AsArray())
            if(!SelectedEdition["clients"]!.AsArray().Any(c=>c!.ToString()==nation!["client"]!.ToString())){error="所选发行版不支持客户端："+nation!["client"];return false;}
        return error=="";
    }
    private async Task StartChecked(string config,string? session=null)
    {
        if(_starting)return;
        _starting=true;
        _hint.Text="正在核对发行版、实验服务与模型配置…";
        try
        {
            await DesktopSetup.Call(SetupRuntime,new JsonObject { ["action"]="validate",["config"]=config,["session"]=session });
            StartRequested?.Invoke(config,session);
        }
        catch(Exception e){_hint.Text=e.Message;}
        finally{_starting=false;}
    }
    private string RulesCaption(JsonObject info)
    {
        var edition=_editions.FirstOrDefault(e=>e!["id"]!.ToString()==info["id"]!.ToString());
        string title=(edition?["name"]??info["id"])!.ToString()+" "+info["version"];
        var features=info["features"]?.AsObject()??new JsonObject();
        return title+" · "+(features.Count==0?"无实验服务":string.Join(" · ",features.Select(p=>
        {
            string name=edition?["features"]?[p.Key]?["name"]?.ToString()??p.Key;
            bool enabled=p.Value is JsonObject state?state["enabled"]!.GetValue<bool>():p.Value!.GetValue<bool>();
            return name+(enabled?" 开":" 关");
        })));
    }
    private async Task CheckSave(string session,string config,Label label,Button resume,Button upgrade)
    {
        try
        {
            var result=await DesktopSetup.Call(SetupRuntime,new JsonObject { ["action"]="inspect_save",["session"]=session,["config"]=config });
            if(!GodotObject.IsInstanceValid(label)||!label.IsInsideTree())return;
            label.Text=RulesCaption(result)+(result["message"]?.ToString() is {Length:>0} message?"\n"+message:"");
            label.TooltipText=label.Text;resume.Disabled=!result["can_resume"]!.GetValue<bool>();
            upgrade.Visible=result["can_upgrade"]!.GetValue<bool>();upgrade.Disabled=false;
        }
        catch(Exception e){if(GodotObject.IsInstanceValid(label)&&label.IsInsideTree())label.Text="无法读取此存档："+e.Message;}
    }
    private async Task UpgradeSave(string session,string config,Button button)
    {
        button.Disabled=true;_hint.Text="正在校验并复制升级，原存档保留…";
        string destination=System.IO.Path.Combine(System.IO.Path.GetDirectoryName(_settingsPath)!,"desktop","upgraded-"+DateTime.Now.ToString("yyyyMMdd-HHmmss")+"-"+Guid.NewGuid().ToString("N")[..6]);
        try
        {
            await DesktopSetup.Call(SetupRuntime,new JsonObject { ["action"]="upgrade_copy",["session"]=session,["config"]=config,["destination"]=destination });
            _hint.Text="已创建升级副本，请从列表继续；原文件与历史保留。";RefreshSaves();
        }
        catch(Exception e){_hint.Text=e.Message;if(GodotObject.IsInstanceValid(button))button.Disabled=false;}
    }
}
