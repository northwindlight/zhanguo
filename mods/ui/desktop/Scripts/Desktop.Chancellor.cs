using Godot;
using System;
using System.Collections.Generic;
using System.Linq;
using System.Text.Json.Nodes;
using static WorldMap;

// 内阁首辅原型：首辅总揽内政、军事、外交并拟写结构化奏折；君主只批阅（准／驳）、下诏（口谕与方针）
// 或亲政（亲手操作）。准奏后的操作逐条经原有真人提交通道执行，回执照旧，首辅不直接写世界。
// 宿主侧见 mods/ui/desktop_chancellor.py。
public partial class Desktop
{
    private sealed class ChancellorItem
    {
        public string Id="",Title="",Reason="",Directive="",Status="pending",Note="";
        public JsonArray Calls=new();
        public bool Major;
        public int Done,Failed;
    }
    private readonly List<ChancellorItem> _chancellorItems=new();
    private readonly List<string> _chancellorDirectives=new(),_chancellorDropped=new();
    private bool _chancellorEnabled,_chancellorBusy,_chancellorFeedbackSent=true,_chancellorPersonal,_chancellorEndAfter;
    private readonly Queue<(string Tag,string Operation,JsonObject Arguments,int Period)> _chancellorRun=new();
    private string _chancellorSummary="",_chancellorReply="",_chancellorStatus="",_chancellorModel="",_humanSubmittedTag="";
    private int _chancellorPeriod=-1;
    private double _chancellorSeconds,_chancellorStarted;
    private static readonly string[] ChancellorExamples={"这回合多建产业，钱留三百","粮食保持一百以上","军队守住边境，不主动开战","闲着的兵去占无主之地"};

    private void SendChancellor(JsonObject row){if(HumanMode||row["command"]?.ToString()=="chancellor_config")SendHumanRow(row);}
    private void ConfigureChancellor()
    {
        if(_menu?.ChancellorProfile is JsonObject profile)
            SendChancellor(new JsonObject { ["command"]="chancellor_config",["profile"]=profile });
    }
    private void RequestChancellor(string kind,string text="")
    {
        if(!_chancellorEnabled){HumanMessage("本局未设首辅。",false);return;}
        if(_chancellorBusy){HumanMessage("首辅正在拟奏，请稍候。",false);return;}
        SendChancellor(new JsonObject { ["command"]="chancellor_request",["kind"]=kind,["text"]=text });
    }

    private bool ReceiveChancellor(JsonObject row)
    {
        string type=S(row["type"]);
        if(type=="chancellor_state")
        {
            _chancellorEnabled=Yes(row["enabled"]);_chancellorBusy=Yes(row["busy"]);_chancellorModel=S(row["model"]);_chancellorPersonal=Yes(row["personal"]);
            _chancellorDirectives.Clear();_chancellorDirectives.AddRange((row["directives"]?.AsArray()??new JsonArray()).Select(d=>S(d)));
            if(_chancellorNavButton!=null)_chancellorNavButton.Visible=_chancellorEnabled;
            // 设定到达时若本回合尚无奏折，补拟一份（首回合设定晚于开局自动拟奏）。
            if(_chancellorEnabled&&!_chancellorPersonal&&!_chancellorBusy&&_humanTurn!=null&&_chancellorPeriod!=I(_humanTurn["period"]))RequestChancellor("memorial");
            RefreshChancellorPage();return true;
        }
        if(type=="chancellor_status")
        {
            _chancellorBusy=true;_chancellorStarted=_elapsed;
            _chancellorStatus=S(row["kind"])=="edict"?"首辅领旨，正在拟奏…":"首辅正在拟写本回合奏折…";
            RefreshChancellorPage();return true;
        }
        if(type!="chancellor_memorial")return false;
        _chancellorBusy=false;_chancellorSeconds=F(row["seconds"]);
        if(!Yes(row["ok"])){_chancellorStatus=S(row["message"]);HumanMessage(_chancellorStatus,false);RefreshChancellorPage();return true;}
        SendChancellorFeedback();
        _chancellorPeriod=I(row["period"]);_chancellorSummary=S(row["summary"]);_chancellorReply=S(row["reply"]);
        _chancellorStatus=$"奏折已呈 · 拟奏 {_chancellorSeconds:0.#} 秒";
        _chancellorItems.Clear();_chancellorDropped.Clear();
        foreach(var p in row["proposals"]?.AsArray()??new JsonArray())
            _chancellorItems.Add(new ChancellorItem { Id=S(p!["id"]),Title=S(p["title"]),Reason=S(p["reason"]),Directive=S(p["directive"]),
                Calls=(JsonArray)(p["calls"]?.DeepClone()??new JsonArray()),Major=Yes(p["major"]) });
        _chancellorDropped.AddRange((row["dropped"]?.AsArray()??new JsonArray()).Select(d=>S(d)));
        _chancellorFeedbackSent=_chancellorItems.Count==0;
        // 奏折到达：抽屉空闲或停在地块页时直接呈上，不打断正在进行的其他页面。
        if(!_humanDrawer.IsOpen||_humanPage is "tile" or "chancellor")OpenHumanPage("chancellor");
        else RefreshChancellorPage();
        return true;
    }
    private void RefreshChancellorPage(){if(HumanMode&&_humanDrawer.IsOpen&&_humanPage=="chancellor")RenderHumanPage();}

    private string ChancellorCallText(JsonNode call)
    {
        string op=S(call["operation"]);var a=call["arguments"]!;
        string at=a["x"]!=null&&a["y"]!=null?$" · ({I(a["x"])}, {I(a["y"])})":"";
        return op switch {
            "build" or "queue_build"=>$"建设 {HumanLabel(S(a["kind"]))}{at}",
            "recruit" or "queue_recruit"=>$"征兵 {HumanLabel(S(a["kind"]))} ×{(a["count"]==null?1:I(a["count"]))}{at}",
            "buy"=>$"买入 {HumanLabel(S(a["resource"]))} ×{I(a["quantity"])}",
            "sell"=>$"卖出 {HumanLabel(S(a["resource"]))} ×{I(a["quantity"])}",
            _=>OperationLabel(op)+ChancellorArgs(a.AsObject()) };
    }

    private string ChancellorArgs(JsonObject a)
    {
        var parts=new List<string>();
        if(a["ids"] is JsonArray ids)parts.Add(string.Join("、",ids.Select(i=>"#"+S(i))));else if(a["id"]!=null)parts.Add("#"+S(a["id"]));
        if(a["target"]!=null)parts.Add(NationLabel(S(a["target"])));
        if(a["x"]!=null&&a["y"]!=null)parts.Add($"({I(a["x"])}, {I(a["y"])})");
        if(a["nodes"] is JsonArray nodes)parts.Add("路线 "+string.Join("→",nodes.Select(n=>$"({S(n!["position"]?[0])},{S(n["position"]?[1])})")));
        if(a["offer"]!=null)parts.Add("提议 #"+S(a["offer"]));
        if(a["resource"]!=null)parts.Add(HumanLabel(S(a["resource"]))+(a["quantity"]!=null?" ×"+S(a["quantity"]):""));
        if(a["text"]!=null)parts.Add("“"+S(a["text"])[..Math.Min(24,S(a["text"]).Length)]+"…”");
        return parts.Count==0?"":" · "+string.Join(" · ",parts);
    }

    private void RenderChancellor()
    {
        _humanDrawer.Title="内阁奏报";
        if(!_chancellorEnabled)
        {
            HumanText("本局未设内阁首辅",true);
            HumanText("开新局时，在「创建你的国家」里选择首辅使用的模型档案（API 档案在主菜单「设置」中添加）。首辅总揽内政、军事与外交，每回合拟奏，你只需批阅、下诏，或亲政。",color:"a2b8ac");
            return;
        }
        var personal=new CheckButton { Name="ChancellorPersonal",Text="亲政（首辅不自动拟奏，我亲手操作）",ButtonPressed=_chancellorPersonal };
        personal.AddThemeFontSizeOverride("font_size",14);_humanContent.AddChild(personal);
        personal.Toggled+=on=>SendChancellor(new JsonObject { ["command"]="chancellor_mode",["personal"]=on });
        var head=HumanText(_chancellorPeriod>=0?$"第 {_chancellorPeriod+1} 回合奏折":"首辅奏折",true);head.Name="ChancellorHeading";
        var status=HumanText(_chancellorBusy?$"{_chancellorStatus}（{Math.Max(0,_elapsed-_chancellorStarted):0} 秒）":_chancellorStatus,color:_chancellorBusy?"e1c48b":"a2b8ac");
        status.Name="ChancellorStatus";status.AddThemeFontSizeOverride("font_size",14);
        if(_chancellorSummary!="")HumanText(_chancellorSummary).Name="ChancellorSummary";
        if(_chancellorReply!="")HumanText("臣启：“"+_chancellorReply+"”",color:"d6cba7").Name="ChancellorReply";
        foreach(var item in _chancellorItems)RenderChancellorItem(item);
        if(_chancellorItems.Count==0&&!_chancellorBusy&&_chancellorPeriod>=0)HumanText("本回合首辅无事可奏。",color:"a2b8ac");
        if(_chancellorDropped.Count>0)
        {
            var dropped=HumanText($"另有 {_chancellorDropped.Count} 条建议不合规，已剔除（悬停查看）",color:"8f9a8f");
            dropped.AddThemeFontSizeOverride("font_size",13);dropped.MouseFilter=Control.MouseFilterEnum.Stop;
            dropped.TooltipText=string.Join("\n",_chancellorDropped);
        }
        var actions=new HBoxContainer();actions.AddThemeConstantOverride("separation",6);_humanContent.AddChild(actions);
        int ready=_chancellorItems.Count(i=>i.Status=="pending"&&!i.Major);
        var all=Button(actions,$"全部准奏（{ready}）",ApproveAllChancellor);all.Name="ChancellorApproveAll";HumanArt.Style(all,"check");
        all.SizeFlagsHorizontal=SizeFlags.ExpandFill;all.Disabled=ready==0||!HumanCanQueue;
        all.TooltipText="依次执行所有未处理的常规建议；宣战、议和、结盟、借贷、拆除、遣散等大事须逐条批阅。";
        int majors=_chancellorItems.Count(i=>i.Status=="pending"&&i.Major);
        var close=Button(actions,"准奏并退朝",()=>{ApproveAllChancellor();_chancellorEndAfter=true;});close.Name="ChancellorApproveAndEnd";HumanArt.Style(close,"end-turn");
        close.Disabled=!HumanCanQueue||_chancellorEndAfter;
        close.TooltipText="准奏全部常规建议，执行完毕后结束本回合"+(majors>0?$"；尚有 {majors} 件大事未批，将视为不办":"");
        var redo=Button(actions,"重新拟奏",()=>RequestChancellor("memorial"));redo.Name="ChancellorRedo";HumanArt.Style(redo,"report");
        redo.Disabled=_chancellorBusy||_humanTurn==null;

        HumanText("下诏",true);
        var edictRow=new HBoxContainer();_humanContent.AddChild(edictRow);
        var edict=new LineEdit { Name="ChancellorEdict",PlaceholderText="例如：东边多建农场；派兵守住北岭；与魏国修好",SizeFlagsHorizontal=SizeFlags.ExpandFill,MaxLength=200 };
        edictRow.AddChild(edict);
        void SendEdict(string text){text=text.Trim();if(text=="")return;RequestChancellor("edict",text);}
        edict.TextSubmitted+=t=>SendEdict(t);
        var send=Button(edictRow,"下诏",()=>SendEdict(edict.Text));send.Name="ChancellorSendEdict";HumanArt.Style(send,"letter");send.Disabled=_chancellorBusy;
        var examples=new HFlowContainer();examples.AddThemeConstantOverride("h_separation",6);_humanContent.AddChild(examples);
        foreach(var text in ChancellorExamples)
        {
            string chosen=text;var chip=new Button { Text=chosen,FocusMode=Control.FocusModeEnum.None,TooltipText="直接下这道诏书" };
            HumanArt.Style(chip);chip.AddThemeFontSizeOverride("font_size",13);chip.Pressed+=()=>SendEdict(chosen);chip.Disabled=_chancellorBusy;examples.AddChild(chip);
        }

        HumanText("方针",true).TooltipText="长期有效，首辅每回合都会遵守；最多 12 条。";
        if(_chancellorDirectives.Count==0)HumanText("尚未定方针。可写「金币低于 300 不再扩军」「东部只建农场和林场」等。",color:"a2b8ac");
        for(int i=0;i<_chancellorDirectives.Count;i++)
        {
            int index=i;var row=new HBoxContainer();_humanContent.AddChild(row);
            var label=new Label { Text=$"{i+1}. {_chancellorDirectives[i]}",SizeFlagsHorizontal=SizeFlags.ExpandFill,AutowrapMode=TextServer.AutowrapMode.WordSmart };
            row.AddChild(label);
            var remove=Button(row,"",()=>SendChancellor(new JsonObject { ["command"]="chancellor_directive",["op"]="remove",["index"]=index }));
            HumanArt.Style(remove,"close");remove.TooltipText="废除这条方针";
        }
        var addRow=new HBoxContainer();_humanContent.AddChild(addRow);
        var directive=new LineEdit { Name="ChancellorDirective",PlaceholderText="新方针",SizeFlagsHorizontal=SizeFlags.ExpandFill,MaxLength=200 };addRow.AddChild(directive);
        void AddDirective(){if(directive.Text.Trim()!="")SendChancellor(new JsonObject { ["command"]="chancellor_directive",["op"]="add",["text"]=directive.Text.Trim() });}
        directive.TextSubmitted+=_=>AddDirective();
        var add=Button(addRow,"立为方针",AddDirective);add.Name="ChancellorAddDirective";HumanArt.Style(add,"check");
    }

    private void RenderChancellorItem(ChancellorItem item)
    {
        var panel=new PanelContainer { Name="Chancellor_"+item.Id };panel.AddThemeStyleboxOverride("panel",HumanArt.Box("panel"));_humanContent.AddChild(panel);
        var body=new VBoxContainer();body.AddThemeConstantOverride("separation",3);panel.AddChild(body);
        var top=new HBoxContainer();body.AddChild(top);
        var title=new Label { Text=(item.Major?"【大事】":"")+item.Title,SizeFlagsHorizontal=SizeFlags.ExpandFill,AutowrapMode=TextServer.AutowrapMode.WordSmart };
        title.AddThemeFontSizeOverride("font_size",17);
        title.AddThemeColorOverride("font_color",new Color(item.Status switch { "done"=>"a2e4a6","failed"=>"ffb6a4","rejected"=>"78837b",_=>"f0d58a" }));
        top.AddChild(title);
        if(item.Status=="pending")
        {
            var yes=Button(top,"准",()=>ApproveChancellor(item));yes.Name="ChancellorApprove_"+item.Id;HumanArt.Style(yes,"check");
            yes.Disabled=!HumanCanQueue&&item.Calls.Count>0;yes.TooltipText=item.Calls.Count>0?"准奏：依次执行下列操作，入队即预付":"准奏：立为方针";
            var no=Button(top,"驳",()=>{item.Status="rejected";RefreshChancellorPage();});no.Name="ChancellorReject_"+item.Id;HumanArt.Style(no,"close");
        }
        else
        {
            var state=new Label { Text=item.Status switch { "done"=>"已办","failed"=>"未成","rejected"=>"已驳",_=>$"执行中 {item.Done+item.Failed}/{item.Calls.Count}" } };
            state.AddThemeFontSizeOverride("font_size",14);state.AddThemeColorOverride("font_color",new Color("a2b8ac"));top.AddChild(state);
        }
        if(item.Reason!="")
        {
            var reason=new Label { Text=item.Reason,AutowrapMode=TextServer.AutowrapMode.WordSmart };
            reason.AddThemeFontSizeOverride("font_size",14);reason.AddThemeColorOverride("font_color",new Color("c9c6b0"));body.AddChild(reason);
        }
        var lines=item.Calls.Select(c=>"· "+ChancellorCallText(c!)).ToList();
        if(item.Directive!="")lines.Add("· 立为方针："+item.Directive);
        var detail=new Label { Text=string.Join("\n",lines),AutowrapMode=TextServer.AutowrapMode.WordSmart };
        detail.AddThemeFontSizeOverride("font_size",14);detail.AddThemeColorOverride("font_color",new Color("a7b8a9"));body.AddChild(detail);
        if(item.Note!="")
        {
            var note=new Label { Text=item.Note,AutowrapMode=TextServer.AutowrapMode.WordSmart,MaxLinesVisible=3,TextOverrunBehavior=TextServer.OverrunBehavior.TrimEllipsis };
            note.AddThemeFontSizeOverride("font_size",13);note.AddThemeColorOverride("font_color",new Color(item.Status=="failed"?"ffb6a4":"a2b8ac"));body.AddChild(note);
        }
    }

    private void ApproveChancellor(ChancellorItem item)
    {
        if(item.Status!="pending")return;
        if(item.Directive!="")SendChancellor(new JsonObject { ["command"]="chancellor_directive",["op"]="add",["text"]=item.Directive });
        if(item.Calls.Count>0&&!HumanCanQueue){HumanMessage("请先继续游戏，或等待本国取得行动权。",false);return;}
        foreach(var call in item.Calls)
            _chancellorRun.Enqueue((item.Id,S(call!["operation"]),(JsonObject)call["arguments"]!.DeepClone(),I(_humanTurn!["period"])));
        item.Status=item.Calls.Count==0?"done":"running";
        RefreshChancellorPage();
    }
    private void ApproveAllChancellor(){foreach(var item in _chancellorItems.Where(i=>i.Status=="pending"&&!i.Major).ToList())ApproveChancellor(item);}

    /// <summary>每帧推进：上一条执行完、演出播完后，再提交下一条准奏操作；全部办完且要求退朝时结束回合。</summary>
    private void PumpChancellor()
    {
        if(!HumanMode)return;
        if(_chancellorRun.Count>0&&_humanTurn!=null&&_chancellorRun.Peek().Period!=I(_humanTurn["period"]))
        {
            foreach(var (tag,_,_,_) in _chancellorRun)ChancellorReceipt(tag,false,"回合已变化，未执行");
            _chancellorRun.Clear();_chancellorEndAfter=false;
        }
        if(!HumanCanWrite||_humanCommands.Count>0||_humanConfirm.Visible)return;
        if(_chancellorRun.Count>0){var (tag,op,args,_)=_chancellorRun.Dequeue();SendHumanCommand(op,args,tag);return;}
        if(_chancellorEndAfter&&_chancellorItems.All(i=>i.Status!="running"))
        {_chancellorEndAfter=false;HumanEndTurn();}
    }

    /// <summary>准奏操作的正式回执逐条记回对应奏折。</summary>
    private void ChancellorReceipt(string tag,bool ok,string text)
    {
        var item=_chancellorItems.FirstOrDefault(i=>i.Id==tag);if(item==null)return;
        if(ok)item.Done++;else{item.Failed++;item.Note=text;}
        if(item.Done+item.Failed>=item.Calls.Count)item.Status=item.Failed>0?"failed":"done";
        RefreshChancellorPage();
    }

    /// <summary>把本份奏折的准驳结果交给宿主记录，供首辅揣摩君主偏好。</summary>
    private void SendChancellorFeedback()
    {
        if(_chancellorFeedbackSent||_chancellorItems.Count==0)return;
        _chancellorFeedbackSent=true;
        SendChancellor(new JsonObject { ["command"]="chancellor_feedback",["period"]=_chancellorPeriod,
            ["approved"]=new JsonArray(_chancellorItems.Where(i=>i.Status is "done" or "running").Select(i=>(JsonNode?)i.Title).ToArray()),
            ["rejected"]=new JsonArray(_chancellorItems.Where(i=>i.Status=="rejected").Select(i=>(JsonNode?)i.Title).ToArray()),
            ["failed"]=new JsonArray(_chancellorItems.Where(i=>i.Status=="failed").Select(i=>(JsonNode?)(i.Title+"："+i.Note)).ToArray()) });
    }
}
