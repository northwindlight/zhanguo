using Godot;
using System;
using System.Collections.Generic;
using System.Linq;
using System.Text.Json.Nodes;
using static WorldMap;
using Zhanguo.Desktop.Layout;

public partial class Desktop
{
    private readonly HashSet<int> _readIntel=new();
    private readonly Dictionary<string,JsonObject> _humanDrafts=new();
    private Label? _humanPreview;
    private static readonly Dictionary<string,string> HumanFieldNames=new() {
        ["target"]="对象",["kind"]="种类",["resource"]="资源",["quantity"]="数量",["count"]="征召数量",["name"]="联盟名称",
        ["text"]="信件正文",["note"]="议和说明",["gold"]="赔款金币",["truce"]="休战周期",["tos"]="邀请国家",["accept"]="投票选择",
        ["offer"]="收到的提议",["vote"]="联盟议案",["bloc"]="联盟",["turn"]="报表覆盖期" };
    private void OpenHumanPage(string page)
    {
        if(!HumanMode)return;
        CancelHumanTarget();
        if(_humanPage!=page)
            HumanMessage(page is "build" or "recruit"?"选择本格亮起的图标执行操作；悬停查看价格和条件。":"操作回执保留在国家面板的操作记录中。");
        _humanPage=page;
        RenderHumanPage();OpenDrawer(_humanDrawer);
    }
    private void SaveHumanDraft()
    {
        if(_formOperation==""||_humanFields.Count==0)return;
        var values=new JsonObject();
        foreach(var (key,value) in _humanFields)values[key]=value()?.DeepClone();
        _humanDrafts[_formOperation]=values;
    }
    private string _formOperation="";
    private void RenderHumanPage()
    {
        if(!HumanMode)return;
        SaveHumanDraft();ResetRts();ClearHumanChildren(_humanContent);_humanWrites.Clear();_humanFields.Clear();_formOperation="";
        _humanContent.AddThemeConstantOverride("separation",_humanPage is "build" or "recruit"?6:10);
        _humanExtra=new JsonObject();_humanPreview=null;_humanSubmit=null;
        switch(_humanPage)
        {
            case "tile":RenderHumanTile();break;
            case "build":case "recruit":RenderRtsCatalog();break;
            case "army":RenderHumanArmyList();break;
            case "nation":RenderHumanNation();break;
            default:RenderHumanActions(_humanPage);break;
        }
        RefreshHumanButtons();
    }
    private Label HumanText(string text,bool heading=false,string color="e5e5d2")
    {
        var label=new Label { Text=text,AutowrapMode=TextServer.AutowrapMode.WordSmart,SizeFlagsHorizontal=SizeFlags.ExpandFill };
        label.AddThemeColorOverride("font_color",new Color(color));if(heading)label.AddThemeFontSizeOverride("font_size",20);
        _humanContent.AddChild(label);return label;
    }
    private Button HumanButton(string text,Action action,string icon="",bool write=false)
    {
        var button=Button(_humanContent,text,action);HumanArt.Style(button,icon);
        if(write){_humanWrites.Add(button);button.Disabled=!HumanCanWrite;}return button;
    }
    private static string Quantity(JsonNode? n)=>NationLedger.Quantity(F(n));
    private string Resources(JsonNode? node)
    {
        if(node==null)return "无";
        if(node is JsonObject obj)return string.Join("、",obj.Select(p=>$"{HumanLabel(p.Key)} ×{Quantity(p.Value)}"));
        if(node is JsonArray arr)return string.Join("、",arr.Select(p=>$"{HumanLabel(S(p![0]))} ×{Quantity(p[1])}"));
        return S(node);
    }
    private string Money(JsonNode? node)=>node==null?"—":node is JsonObject obj?(obj["total"]==null?"未完整计价":Quantity(obj["total"])):Quantity(node);
    private string VisibilityText(JsonObject tile)=>S(tile["visibility"]) switch { "unknown"=>"未知区域：没有地形、建筑或敌军情报。", "memory"=>$"历史地图 · 采样第 {I(tile["sampled"])+1} 期 · 静态示意",_=>"当前实时视野" };
    private void RenderHumanTile()
    {
        var tile=_map.Frame?["tiles"]?.AsArray().FirstOrDefault(t=>I(t!["position"]![0])==_humanCell.X&&I(t["position"]![1])==_humanCell.Y)?.AsObject();
        if(tile==null){HumanText("点击地图上的地块查看详情。",true);return;}
        string state=S(tile["visibility"]);
        HumanText($"{(state=="unknown"?"未探明地块":S(tile["name"]) is {Length:>0} n?n:S(tile["terrain_name"]))}  ({_humanCell.X}, {_humanCell.Y})",true);
        HumanText(VisibilityText(tile),color:"a2b8ac");
        if(state!="unknown")
        {
            HumanText($"{tile["terrain_name"]} · {(S(tile["owner"])==""?"无主之地":NationLabel(S(tile["owner"]))) }");
            if(tile["passable"]?.GetValue<bool>()==false)HumanText("自然屏障：不可通行、占领或建设。",color:"e5b389");
            RenderHumanInventory(tile);
            if(state=="memory"||S(tile["owner"])!=HumanActor)
            {
                var dates=tile["knowledge"]!.AsObject().Where(p=>p.Key.StartsWith("building/")&&I(p.Value?["value"]?["count"])+I(p.Value?["value"]?["construction"])>0);
                foreach(var p in dates)HumanText($"{HumanLabel(p.Key[9..])} · 第 {I(p.Value!["sampled"])+1} 期 · {MapSource(S(p.Value["source"]))}",color:"a2b8ac");
            }
            else if(S(tile["owner"])==HumanActor)
            {
                RenderHumanCapacities(tile);
                HumanButton("在这里建设",()=>OpenHumanPage("build"),"build");
                if(HasRecruitFacility(tile))HumanButton("在这里征兵",()=>OpenHumanPage("recruit"),"army");
                else HumanText("征兵需要本格已落成的兵营／军屯。",color:"a2ad9d");
            }
        }
        HumanText("驻军",true);
        var local=(HumanFacts["army"]?.AsArray()??new JsonArray()).Where(u=>I(u!["x"])==_humanCell.X&&I(u["y"])==_humanCell.Y).ToArray();
        var own=local.Where(u=>S(u!["owner"])==HumanActor).ToArray();
        if(own.Length>0)
        {
            var all=new CheckButton { Text="全选本国驻军",ButtonPressed=own.All(u=>_selectedArmies.Contains(I(u!["id"]))) };
            _humanContent.AddChild(all);all.Toggled+=on=>{foreach(var u in own)if(on)_selectedArmies.Add(I(u!["id"]));else _selectedArmies.Remove(I(u!["id"]));RenderHumanPage();};
            foreach(var unit in own)
            {
                int id=I(unit!["id"]);string reason=UnitReason(unit);
                var check=new CheckButton { Name=$"HumanArmy{id}",Text=$"#{id} {HumanLabel(S(unit["kind"]))} · HP {Quantity(unit["hp"])}/{Quantity(HumanFacts["unit_catalog"]?[S(unit["kind"])]?["hp"])}\n{(reason==""?"可以行动":reason)}",
                    ButtonPressed=_selectedArmies.Contains(id) };
                check.TooltipText=reason==""?"移动与攻击须分别选择目标":reason+"；原地攻击等例外仍由具体军令判断";
                check.AddThemeColorOverride("font_color",new Color(reason==""?"d6e6c7":"a4aca5"));
                check.AddThemeIconOverride("checked",HumanArt.Texture("checkbox-on"));check.AddThemeIconOverride("unchecked",HumanArt.Texture("checkbox-off"));
                check.Toggled+=on=>{if(on)_selectedArmies.Add(id);else _selectedArmies.Remove(id);};
                var soldierRow=new HBoxContainer();_humanContent.AddChild(soldierRow);
                soldierRow.AddChild(new TextureRect { Texture=RtsArt.Unit(S(unit["kind"]),_map.NationColor(HumanActor)),CustomMinimumSize=new Vector2(28,40),ExpandMode=TextureRect.ExpandModeEnum.IgnoreSize,StretchMode=TextureRect.StretchModeEnum.KeepAspectCentered });
                check.SizeFlagsHorizontal=SizeFlags.ExpandFill;soldierRow.AddChild(check);
            }
            foreach(var (mode,text,icon) in new[]{("move","移动 · 蓝色空心圈","move"),("attack","攻击 · 红色准星圈","attack"),("retreat","撤退 · 回合末撤出","retreat")})
            {string command=mode;HumanButton(text,()=>StartHumanTarget(command),icon,true);}
            HumanButton("遣散选中军队",()=>ConfirmHuman("遣散军队","选中的本国军队将被遣散，材料不退还；交战限制以实际回执为准。",()=>SubmitHuman("disband",new JsonObject { ["ids"]=new JsonArray(_selectedArmies.Order().Select(i=>(JsonNode?)JsonValue.Create(i)).ToArray()) })),"disband",true);
        }
        foreach(var u in local.Where(u=>S(u!["owner"])!=HumanActor))HumanText($"{(S(u!["owner"])=="野人"?"野人":NationLabel(S(u["owner"])))} · {HumanLabel(S(u["kind"]))} · HP {Quantity(u["hp"])}",color:"d4b9a0");
        if(local.Length==0)HumanText(state=="live"?"没有可见驻军":"没有己方驻军；这里没有实时敌军情报。");
    }
    private string UnitReason(JsonNode u)=>I(u["born_turn"])==_period&&u["born_turn"]!=null?"新募：本回合不能行动":
        u["moved_turn"]!=null&&I(u["moved_turn"])==_period?"已用行动额度（原地攻击另判）":u["engaged"]?.GetValue<bool>()==true?"正在交战（可检查原地攻击或撤退）":"";
    private string MapSource(string source)
    {
        if(source=="vision")return "合法视野采样";
        var parts=source.Split('/');return parts.Length>1?$"来自{NationLabel(parts[1])}的{(parts[0]=="spy"?"间谍报告":"地图")}":"已取得情报";
    }
    private void RenderHumanArmyList()
    {
        HumanText("本国军队",true);HumanText("先定位地块，再在驻军面板中勾选。地图上的小人只负责演出。",color:"a2b8ac");
        foreach(var group in OwnArmies.GroupBy(u=>(X:I(u["x"]),Y:I(u["y"]))))
        {
            var p=new Vector2I(group.Key.X,group.Key.Y);double hp=group.Sum(u=>F(u["hp"]));
            HumanButton($"({p.X}, {p.Y}) · {group.Count()} 支 · HP {hp:G5}",()=>{_humanCell=p;_map.SelectAndFocus(p);OpenHumanPage("tile");},"pin");
        }
        if(!OwnArmies.Any())HumanText("暂无军队。先建设兵营或军屯，落成后再征召。",color:"d6cba7");
        HumanButton("征召军队",()=>{_humanOperation="recruit";OpenHumanPage("recruit");},"army");
    }
    private void RenderHumanNation()
    {
        HumanText($"{NationLabel(HumanActor)} · 国务",true);
        HumanText("当前库存\n"+string.Join("\n",HumanFacts["econ"]!["stocks"]!.AsArray().Select(s=>$"{HumanLabel(S(s!["id"]))}  {Quantity(s["amount"])}")));
        var e=HumanFacts["econ"]!["preview"]!;
        HumanText($"能源预演：产能 {e["energy"]?["capacity"]} / 需求 {e["energy"]?["demand"]}\n生产与军需预演（非实收）：\n{Resources(e["outputs"])}\n需求：{Resources(e["inputs"])}");
        var bank=HumanFacts["bank"]!;
        HumanText($"贷款额度 {bank["credit_limit"]} 金 · 当前债务 {bank["liabilities"]} 金\n存款利率 {F(bank["savings_rate"]):P1} · 贷款利率 {F(bank["loan_rate"]):P1}");
        HumanButton("贷款与只读授信预览",()=>{_humanOperation="borrow";OpenHumanPage("finance");},"market");
        HumanText("本国已发布报表",true);
        var reports=_humanState!["reports"]!.AsArray();if(reports.Count==0)HumanText("尚未到报表发布期。只展示已经发布的实际报表。");
        foreach(var r in reports)HumanText(EconomicReport(r!));
        HumanText("本次会话操作记录",true);
        HumanText(_humanHistory.Count==0?"尚无操作":string.Join("\n\n",_humanHistory.TakeLast(12)));
    }
    private string EconomicReport(JsonNode r)=>$"{NationLabel(S(r["owner"]))} · 第 {r["start"]}—{r["end"]} 期\nGDP {Money(r["gdp"])} · 投资 {Money(r["investment"])} · 军费 {Money(r["military"])}\n总资产 {Money(r["total_assets"])} · 净资产 {Money(r["net_assets"])}";

    private void RenderHumanActions(string page)
    {
        var allowed=page switch {
            "build"=>new[]{"build"},"recruit"=>new[]{"recruit"},"market"=>new[]{"buy","sell"},"finance"=>new[]{"borrow"},
            "diplomacy"=>new[]{"send_letter","gift","guarantee","cancel_guarantee","propose_defense","break_defense","declare_war","offer_peace","accept_peace","reject_peace","accept_proposal","reject_proposal","propose_vassalage","submit_vassalage","release_vassal","independence_war"},
            "alliance"=>new[]{"bloc_found","bloc_join","bloc_leave","bloc_dissolve","bloc_rename","bloc_transfer","vote"},
            _=>new[]{"buy_map","spy","buy_report","share_map"} };
        var choices=HumanOperations.Where(o=>allowed.Contains(S(o!["operation"]))).ToArray();
        HumanText(page switch { "build"=>"建设","recruit"=>"征召","market"=>"市场","diplomacy"=>"外交","alliance"=>"联盟","finance"=>"国家贷款",_=>"地图与情报" },true);
        if(page=="diplomacy"||page=="alliance")RenderRelationsSummary(page);
        if(choices.Length>0)
        {
            if(!choices.Any(o=>S(o!["operation"])==_humanOperation))_humanOperation=S(choices[0]!["operation"]);
            var pick=new OptionButton { Name="HumanAction",SizeFlagsHorizontal=SizeFlags.ExpandFill };
            foreach(var o in choices)pick.AddItem(S(o!["label"]));
            pick.Selected=Array.FindIndex(choices,o=>S(o!["operation"])==_humanOperation);
            _humanContent.AddChild(pick);
            pick.ItemSelected+=i=>{SaveHumanDraft();_humanOperation=S(choices[(int)i]!["operation"]);RenderHumanPage();};
            RenderHumanForm(choices.First(o=>S(o!["operation"])==_humanOperation)!.AsObject());
        }
        else HumanText("当前政体没有此类操作。");
        if(page=="intel")RenderHumanIntel();
        if(page=="market")
            foreach(var p in _map.Frame!["market"]!.AsArray())HumanText($"{p!["name"]} · 库存 {Quantity(p["inventory"])} · 买 {DesktopScene.PriceText(p["buy"]==null?null:F(p["buy"]))} / 卖 {DesktopScene.PriceText(F(p["sell"]))}",color:"b2c6b5");
    }
    private void RenderRelationsSummary(string page)
    {
        var d=HumanFacts["foreign"]!["diplomacy"]!;
        if(page=="alliance")
        {
            foreach(var b in d["alliances"]!.AsArray())HumanText($"{b!["name"]} · 盟主{NationLabel(S(b["chief"]))}\n{string.Join("、",b["members"]!.AsArray().Select(n=>NationLabel(S(n))))}",color:"bacfb8");
            foreach(var vote in d["votes"]!.AsArray())HumanText($"联盟议案 #{vote!["id"]} · {OperationLabel(S(vote["action"]))}\n开放期：第 {I(vote["deadline"])+1} 回合 · 本国选择：{VoteText(vote["ballots"])}",color:"d6cba7");
        }
        else
        {
            foreach(var n in HumanFacts["foreign"]!["countries"]!.AsArray())HumanText($"{n!["name"]} · {S(n["relation"]) switch { "war"=>"交战", "ally"=>"同盟",_=>"中立" }}",color:"b8cdb6");
            foreach(var o in _humanState!["pending_end"]!["offers"]!.AsArray())HumanText($"待回应：{OfferLabel(o!)}",color:"ecd39d");
        }
    }
    private string OperationLabel(string op)=>HumanOperations.FirstOrDefault(o=>S(o!["operation"])==op)?["label"]?.ToString()??op switch { "found"=>"创建联盟","defense"=>"共同防御","peace"=>"议和","vassal"=>"附庸",_=>"外交事项" };
    private string OfferLabel(JsonNode o)=>$"{OperationLabel(S(o["action"]))} · 来自{NationLabel(S(o["actor"]))}";
    private string VoteText(JsonNode? rows)
    {
        var ballot=rows?.AsArray().FirstOrDefault(b=>S(b![0])==HumanActor);
        return ballot==null?"尚未表态":ballot[1]==null?"弃权":ballot[1]!.GetValue<bool>()?"赞成":"反对";
    }
    private void RenderHumanForm(JsonObject operation)
    {
        _formOperation=S(operation["operation"]);var draft=_humanDrafts.GetValueOrDefault(_formOperation);
        var props=operation["parameters"]!["properties"]!.AsObject();
        foreach(var (key,prop) in props)
        {
            if(key is "site" or "place" or "x" or "y" or "id" or "ids" or "confirm")continue;
            if(key=="bloc"&&_formOperation=="bloc_found")
            {_humanExtra[key]="scenario:human-union-"+Guid.NewGuid().ToString("N")[..10];continue;}
            if(key=="turn")continue; // Latest published economic report, selected by country.
            HumanText(HumanFieldNames.GetValueOrDefault(key,key));
            string type=S(prop?["type"]);
            if(key=="tos")
            {
                var checks=new List<(CheckButton Check,string Id)>();
                foreach(var n in HumanFacts["foreign"]!["countries"]!.AsArray())
                {string id=S(n!["id"]);var c=new CheckButton { Text=S(n["name"]),ButtonPressed=draft?[key]?.AsArray().Any(v=>S(v)==id)==true };_humanContent.AddChild(c);checks.Add((c,id));c.Toggled+=_=>RefreshHumanButtons();}
                _humanFields[key]=()=>new JsonArray(checks.Where(c=>c.Check.ButtonPressed).Select(c=>(JsonNode?)JsonValue.Create(c.Id)).ToArray());continue;
            }
            var options=HumanFieldOptions(key);
            if(options!=null)
            {
                var pick=new OptionButton { Name="HumanField_"+key,SizeFlagsHorizontal=SizeFlags.ExpandFill };
                foreach(var option in options)pick.AddItem(option.Label);
                if(options.Count==0){pick.AddItem("当前没有可选项");pick.Disabled=true;}
                int selected=draft?[key]!=null?options.FindIndex(o=>S(o.Value)==S(draft[key])):-1;
                pick.Selected=Math.Max(0,selected);_humanContent.AddChild(pick);
                _humanFields[key]=()=>options.Count==0?null:options[pick.Selected].Value?.DeepClone();
                pick.ItemSelected+=_=>UpdateHumanPreview();continue;
            }
            if(key is "text" or "note")
            {
                var input=new TextEdit { Name="HumanField_"+key,Text=S(draft?[key]),CustomMinimumSize=new Vector2(0,104),WrapMode=TextEdit.LineWrappingMode.Boundary };
                _humanContent.AddChild(input);_humanFields[key]=()=>JsonValue.Create(input.Text);input.TextChanged+=RefreshHumanButtons;continue;
            }
            if(type=="integer")
            {
                double initial=key=="truce"?5:1;
                var number=new SpinBox { Name="HumanField_"+key,MinValue=key=="gold"?0:1,MaxValue=1_000_000,Value=draft?[key]!=null?F(draft[key]):initial,Step=1 };
                _humanContent.AddChild(number);_humanFields[key]=()=>JsonValue.Create((int)number.Value);
                number.ValueChanged+=_=>UpdateHumanPreview();continue;
            }
            var field=new LineEdit { Name="HumanField_"+key,Text=draft?[key]?.ToString()??(key=="name"?"新联盟":""),PlaceholderText=key=="name"?"输入联盟名称":"填写正文" };
            _humanContent.AddChild(field);_humanFields[key]=()=>JsonValue.Create(field.Text);
            field.TextChanged+=_=>RefreshHumanButtons();
        }
        if(_formOperation is "build" or "recruit")
        {
            HumanText("本国地块");
            var sites=HumanFacts["building_sites"]!["sites"]!.AsArray();
            var pick=new OptionButton { Name="HumanField_location",SizeFlagsHorizontal=SizeFlags.ExpandFill };
            foreach(var s in sites)
            {
                var p=s!["position"]!;var tile=_map.Frame!["tiles"]!.AsArray().FirstOrDefault(t=>I(t!["position"]![0])==I(p[0])&&I(t["position"]![1])==I(p[1]));
                pick.AddItem($"{tile?["name"]} ({p[0]}, {p[1]})");
            }
            int index=Array.FindIndex(sites.ToArray(),s=>I(s!["position"]![0])==_humanCell.X&&I(s["position"]![1])==_humanCell.Y);
            pick.Selected=Math.Max(0,index);_humanContent.AddChild(pick);
            _humanFields["x"]=()=>sites.Count==0?null:sites[pick.Selected]!["position"]![0]!.DeepClone();
            _humanFields["y"]=()=>sites.Count==0?null:sites[pick.Selected]!["position"]![1]!.DeepClone();
            pick.ItemSelected+=_=>UpdateHumanPreview();
        }
        _humanPreview=HumanText("",color:"d8c89e");
        if(_formOperation is "build" or "recruit" or "buy" or "sell" or "send_letter" or "borrow")
            HumanButton("查看实际费用预览",RequestHumanPreview,"report");
        string captured=_formOperation;
        _humanSubmit=HumanButton(OperationLabel(captured),()=>
        {
            var args=CollectHumanArguments();
            if(captured is "bloc_leave" or "bloc_dissolve" or "submit_vassalage" or "independence_war" or "release_vassal")
                ConfirmHuman("确认"+OperationLabel(captured),"该操作会改变联盟、贡赋或战争关系，按实际外交结算生效。",()=>SubmitHuman(captured,args));
            else SubmitHuman(captured,args);
        },_humanPage is "build"?"build":_humanPage is "market"?"market":"check",true);
        UpdateHumanPreview();
    }
    private sealed record HumanOption(string Label,JsonNode? Value);
    private List<HumanOption>? HumanFieldOptions(string key)
    {
        var d=HumanFacts["foreign"]!["diplomacy"]!;
        if(key=="target")
        {
            if(_formOperation=="buy_map")return _humanState!["map_offers"]!.AsArray().Where(o=>o!["purchased"]?.GetValue<bool>()!=true)
                .Select(o=>new HumanOption($"{NationLabel(S(o!["target"]))} · 第 {I(o["sampled"])+1} 期 · {o["tiles"]}格 · {o["price"]}金",o["target"])).ToList();
            var targets=HumanFacts["foreign"]!["countries"]!.AsArray().Where(n=>_formOperation!="bloc_transfer"||d["alliances"]!.AsArray().Any(b=>b!["members"]!.AsArray().Any(m=>S(m)==HumanActor)&&b["members"]!.AsArray().Any(m=>S(m)==S(n!["id"]))));
            return targets.Select(n=>new HumanOption(S(n!["name"]),n["id"])).ToList();
        }
        if(key=="bloc")return d["alliances"]!.AsArray().Select(b=>new HumanOption(S(b!["name"]),b["id"])).ToList();
        if(key=="offer")return d["offers"]!.AsArray().Where(o=>_formOperation.Contains("peace")?S(o!["action"])=="peace":S(o!["action"])!="peace")
            .Where(o=>o![_formOperation.StartsWith("accept")?"can_accept":"can_reject"]?.GetValue<bool>()==true).Select(o=>new HumanOption(OfferLabel(o!),o!["id"])).ToList();
        if(key=="vote")return d["votes"]!.AsArray().Where(v=>I(v!["deadline"])==_period).Select(v=>new HumanOption($"议案 #{v!["id"]} · {OperationLabel(S(v["action"]))}",v["id"])).ToList();
        if(key=="accept")return new(){new("赞成",JsonValue.Create(true)),new("反对",JsonValue.Create(false)),new("弃权",null)};
        if(key=="resource")
        {
            IEnumerable<string> keys=_formOperation=="gift"?HumanFacts["econ"]!["stocks"]!.AsArray().Select(s=>S(s!["id"])):_map.Frame!["market"]!.AsArray().Select(s=>S(s!["id"]));
            return keys.Select(id=>new HumanOption(HumanLabel(id),JsonValue.Create(id))).ToList();
        }
        if(key=="kind")
        {
            if(_formOperation=="build")return HumanFacts["building_catalog"]!.AsArray().Select(b=>new HumanOption(HumanLabel(S(b!["id"])),b["id"])).ToList();
            if(_formOperation=="recruit")return HumanFacts["unit_catalog"]!.AsObject().Select(p=>new HumanOption(HumanLabel(p.Key),JsonValue.Create(p.Key))).ToList();
            if(_formOperation=="offer_peace")return new(){new("白和",JsonValue.Create("white")),new("我方赔款",JsonValue.Create("pay")),new("要求赔款",JsonValue.Create("demand")),new("我方称臣",JsonValue.Create("submit")),new("要求对方称臣",JsonValue.Create("subjugate"))};
        }
        return null;
    }
    private JsonObject CollectHumanArguments()
    {
        var result=(JsonObject)_humanExtra.DeepClone();
        foreach(var (key,get) in _humanFields)result[key]=get()?.DeepClone();
        return result;
    }
    private bool HumanFormReady()
    {
        if(_formOperation=="")return false;
        var operation=HumanOperations.FirstOrDefault(o=>S(o!["operation"])==_formOperation);
        if(operation==null)return false;
        var args=CollectHumanArguments();
        foreach(var field in operation["parameters"]?["required"]?.AsArray()??new JsonArray())
        {
            string key=S(field);if(key=="accept")continue;
            if(args[key]==null||args[key] is JsonArray a&&a.Count==0||args[key] is JsonValue&&S(args[key]).Trim()=="")return false;
        }
        return true;
    }
    private void UpdateHumanPreview()
    {
        if(_humanPreview==null)return;var args=CollectHumanArguments();
        if(_formOperation=="build")
        {
            var b=HumanFacts["building_catalog"]!.AsArray().FirstOrDefault(b=>S(b!["id"])==S(args["kind"]));
            _humanPreview.Text=$"基础材料：{Resources(b?["costs_by_level"]?[0])}\n门槛：已有建筑≥{b?["min_slots"]}，每格上限 {b?["max_per_site"]?.ToString()??"不限"}\n资源要求：{(b?["site_resource"]==null?"无":HumanLabel(S(b["site_resource"])))} · 本期末落成，下期起生产。\n投入 {Resources(b?["inputs"])} → 产出 {Resources(b?["outputs"])}\n地形、等级与折扣后的实付金额可用预览核对。";
        }
        else if(_formOperation=="recruit")_humanPreview.Text="只在已落成的兵营／军屯征召；实际额度和材料见预览。新兵本回合不能行动。";
        else if(_formOperation is "buy" or "sell")
        {
            string resource=S(args["resource"]);var p=_map.Frame!["market"]!.AsArray().FirstOrDefault(p=>S(p!["id"])==resource);
            _humanPreview.Text=$"参考{(_formOperation=="buy"?"买价":"卖价")}：{DesktopScene.PriceText(p?[_formOperation=="buy"?"buy":"sell"]==null?null:F(p![_formOperation=="buy"?"buy":"sell"]))} 金\n整单含深度与取整；请查看整单预览，成交后以回执为准。";
        }
        else if(_formOperation=="buy_map")_humanPreview.Text="购买的是注明采样期的历史测绘；同版本只买一次，不扩大实时视野。成功后直接合入大地图。";
        else if(_formOperation=="spy")_humanPreview.Text="付费派谍，报告按实际在途时间送达；不显示对方军队位置。";
        else if(_formOperation=="share_map")_humanPreview.Text="回合末采样派发，实际送达后对方才取得地图；不扩大永久实时视野。";
        else if(_formOperation=="buy_report")_humanPreview.Text="购买该国最新已发布的经济报表，与地图购买分开；未到发布期不能提前取得。";
        else if(_formOperation=="declare_war")_humanPreview.Text="先查看战前自检，再确认提交。宣战仍在外交结算生效，不会立即开战。";
        else if(_formOperation.StartsWith("bloc_"))_humanPreview.Text="联盟提议、投票与实际生效分别处理；加入联盟将影响条约、视野和对外权利。";
        else _humanPreview.Text="提交后查看实际回执；已提交不等于对方接受或已经生效。";
        RefreshHumanButtons();
    }
    private void RequestHumanPreview()
    {
        string operation=_formOperation;var args=CollectHumanArguments();JsonObject query;
        if(operation is "buy" or "sell")query=new JsonObject { ["panel"]="trade_quote",["side"]=operation,["resource"]=args["resource"]?.DeepClone(),["quantity"]=args["quantity"]?.DeepClone() };
        else
        {
            query=new JsonObject { ["panel"]="quote",["action"]=operation=="send_letter"?"letter":operation };
            foreach(var key in new[]{"kind","count","target","text","x","y"})if(args[key]!=null)query[key]=args[key]!.DeepClone();
        }
        HumanQuery("panel",query,row=>
        {
            if(_formOperation!=operation||_humanPreview==null)return;
            var data=row["data"]!;
            _humanPreview.Text=data["error"]!=null?"预览："+LocalHumanError(S(data["error"])):
                operation is "buy" or "sell"?$"整单数量 {data["quantity"]} · 实付／收入 {data["gold"]??data["total"]??data["exact_total"]} 金（仅预览）":
                operation=="borrow"?$"授信 {data["limit"]??data["credit_limit"]} · 手续费 {data["fee"]} · {data["message"]}":
                operation=="send_letter"?$"信件 {data["length"]} 字 · 费用 {data["fee"]} 金":
                $"{(operation=="build"?$"{(S(args["kind"])=="native:castle"?$"{data["level"]}级 · ":"")}本期末落成":"本期可征额度 "+S(data["quota"]))}\n实际材料：{Resources(data["costs"])}";
        });
    }
    private void RenderHumanIntel()
    {
        HumanText("已取得的报告与来信",true);
        foreach(var message in HumanFacts["intel"]!.AsArray().Reverse())
        {
            var m=message!;_readIntel.Add(I(m["id"]));string kind=S(m["kind"]);var payload=m["payload"]!;
            HumanText($"{NationLabel(S(m["source"]))} · 第 {I(m["sampled"])+1} 期 · {kind switch { "map"=>"地图","spy"=>"间谍报告","gift"=>"赠礼",_=>"来信" }}",true);
            if(kind is "letter" or "observer-letter")HumanText(S(payload["text"]));
            else if(kind=="gift")HumanText($"收到 {HumanLabel(S(payload["resource"]))} ×{payload["quantity"]}");
            else
            {
                var tiles=payload["tiles"] as JsonArray;HumanText($"覆盖 {tiles?.Count??0} 格 · 已合入世界地图（历史快照）");
                if(tiles?.Count>0)HumanButton("定位这份地图",()=>{var p=tiles[0]!["position"]!;_map.SelectAndFocus(new(I(p[0]),I(p[1])));},"pin");
                if(payload["inventory"]!=null)HumanText("采样库存："+Resources(payload["inventory"]));
                if(payload["unit_counts"]!=null)HumanText("采样兵种总量："+Resources(payload["unit_counts"])+"（不代表位置）");
            }
        }
        foreach(var purchase in HumanFacts["bank"]!["purchases"]!.AsArray())HumanText("已购经济报表\n"+EconomicReport(purchase!["report"]!));
        _humanNav["intel"].Text="情报";
        HumanText("在途任务",true);
        foreach(var d in HumanFacts["dispatches"]!.AsArray())HumanText($"{OperationLabel(S(d!["kind"]))} → {NationLabel(S(d["target"]))} · 预计第 {I(d["arrive"])+1} 期送达");
    }
    private static string LocalHumanError(string message)=>message switch {
        "Site does not belong to actor"=>"该地块不属于本国。","One construction order per site per period"=>"此地本期已经下过建设指令。",
        "Owner self-build limit reached"=>"已达本国此类建筑的自建上限。","Summary is too short"=>"当前回合结束条件未满足。",_=>message };
    private string FormatHumanReceipt(JsonObject r)
    {
        if(r["ok"]?.GetValue<bool>()!=true)return "操作未成功："+LocalHumanError(S(r["message"]));
        var d=r["data"];string title=OperationLabel(_humanSubmittedOperation);
        if(d is JsonArray units)return title+"："+string.Join("、",units.Select(u=>u?["kind"]!=null?$"{HumanLabel(S(u["kind"]))} #{u["id"]}":S(u?["message"])));
        if(S(r["status"])=="confirmation_required")
        {
            return "请核对宣战窗口中的目标、参战方和条约。确认后才会真正提交宣战；返回可取消。";
        }
        if(_humanSubmittedOperation is "buy" or "sell")return $"{title} {HumanLabel(S(d?["resource"]))} ×{d?["quantity"]} · {(_humanSubmittedOperation=="buy"?"支付":"收入")} {d?["gold"]} 金";
        if(_humanSubmittedOperation=="build")return $"{title} {HumanLabel(S(d?["kind"]))} · 已动工\n实际材料：{Resources(d?["costs"])}";
        if(_humanSubmittedOperation=="buy_map")return $"地图已取得：{NationLabel(S(d?["target"]))} · 第 {I(d?["sampled"])+1} 期 · {d?["tiles"]} 格 · 支付 {d?["price"]} 金";
        if(_humanSubmittedOperation is "move" or "attack" or "retreat" or "disband")return title+"："+(S(d?["message"]) is {Length:>0} text?text:"实际结果已在地图与驻军面板同步")+
            string.Concat((d?["items"]?.AsArray()??new JsonArray()).Select(i=>$"\n#{i!["aid"]}：{i["message"]}"));
        if(_humanSubmittedOperation=="end_turn")return "本国行动结束。等待其他国家行动与本期统一结算。";
        return title+"："+(S(d?["message"]) is {Length:>0} message?message:S(d?["status"]) is {Length:>0} status?StatusText(status):StatusText(S(r["status"])))+
            (d?["fee"]!=null?$" · 费用 {d["fee"]} 金":"")+(d?["arrive"]!=null?$" · 预计第 {I(d["arrive"])+1} 期送达":"");
    }
    private static string StatusText(string status)=>status switch { "submitted"=>"已提交，等待结算","effective"=>"已执行","delivered"=>"已送达","failed"=>"未成功（以回执说明为准）",_=>"已处理" };
}
