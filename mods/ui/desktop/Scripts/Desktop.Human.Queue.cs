using Godot;
using System;
using System.Collections.Generic;
using System.Linq;
using System.Text.Json.Nodes;
using static WorldMap;

public partial class Desktop
{
    private sealed record HumanCommand(string Operation,JsonObject Arguments,string Actor,int Period,string Seat);
    private readonly Queue<HumanCommand> _humanCommands=new();
    private HBoxContainer _humanQueuePanel=null!;
    private Label _humanQueueStatus=null!;
    private static bool QueueableHuman(string operation)=>operation is "buy" or "sell" or "build" or "recruit";
    private bool HumanCanQueue=>HumanMode&&_humanTurn!=null&&!_humanNeedsFrame&&!_humanEnded&&!_humanPaused&&
        !_pauseMenu.Visible&&!_closing&&!_stopped&&_humanState?["can_act"]?.GetValue<bool>()==true&&
        (!HasTimeline||_timelineStage=="action")&&(!_humanBusy||_humanSubmittedOperation!="end_turn");
    private bool PendingHumanSite(string operation,Vector2I at)=>
        (_humanBusy&&_humanSubmittedOperation==operation&&_humanSubmittedCell==at)||
        _humanCommands.Any(c=>c.Operation==operation&&I(c.Arguments["x"])==at.X&&I(c.Arguments["y"])==at.Y);

    private void BuildHumanQueue()
    {
        _humanQueuePanel=new HBoxContainer { Visible=false };_humanDrawer.Body.AddChild(_humanQueuePanel);
        _humanQueueStatus=new Label { Name="HumanQueueStatus",SizeFlagsHorizontal=SizeFlags.ExpandFill,AutowrapMode=TextServer.AutowrapMode.WordSmart,Modulate=new Color("e5d39b") };
        _humanQueueStatus.AddThemeFontSizeOverride("font_size",14);_humanQueuePanel.AddChild(_humanQueueStatus);
        var cancel=Button(_humanQueuePanel,"取消待执行",()=>{_humanCommands.Clear();RefreshHumanQueue();RefreshHumanButtons();});
        cancel.Name="CancelQueuedHuman";HumanArt.Style(cancel,"disband");cancel.AddThemeFontSizeOverride("font_size",14);
        cancel.TooltipText="取消尚未提交的指令；已经执行的交易不会撤销";
    }
    private void RefreshHumanQueue()
    {
        if(_humanQueuePanel==null)return;
        _humanQueuePanel.Visible=_humanCommands.Count>0;
        _humanQueueStatus.Text=$"待执行 {_humanCommands.Count} 项\n按执行时库存、价格和建设条件逐笔核算。";
    }
    private void QueueHumanCommand(string operation,JsonObject arguments)
    {
        if(!HumanCanQueue){HumanMessage("请先继续游戏，或等待本国取得行动权。",false);return;}
        if(_humanCommands.Count>=99){HumanMessage("待执行指令已达99项，请先完成或取消。",false);return;}
        if(operation is "build" or "recruit"&&PendingHumanSite(operation,new(I(arguments["x"]),I(arguments["y"]))))
        {HumanMessage("本格已有同类指令正在执行或排队，请先查看结果。",false);return;}
        _humanCommands.Enqueue(new(operation,(JsonObject)arguments.DeepClone(),HumanActor,I(_humanTurn!["period"]),S(_humanTurn["seat_token"])));
        RefreshHumanQueue();RefreshHumanButtons();DispatchHumanQueue();
    }
    private void DispatchHumanQueue()
    {
        if(_humanCommands.Count==0||!HumanCanWrite)return;
        var command=_humanCommands.Dequeue();RefreshHumanQueue();
        if(command.Actor!=HumanActor||command.Period!=I(_humanTurn!["period"])||command.Seat!=S(_humanTurn["seat_token"]))
        {_humanCommands.Clear();RefreshHumanQueue();HumanMessage("行动权已变化，未提交的旧指令已取消。",false);return;}
        SendHumanCommand(command.Operation,command.Arguments);
    }
}
