using Godot;
using System;
using System.Linq;
using System.Text.Json.Nodes;
using System.Threading.Tasks;
using static WorldMap;

public partial class Desktop
{
    private JsonArray QueuedConstruction()=>_humanState?["production_queue"]?["construction"] as JsonArray??new JsonArray();
    private async Task ReviewProductionQueue(string stem,Vector2I at)
    {
        _humanCell=at;OpenHumanPage("build");await RtsReady();await HumanIdle();
        if(_targetSummary.Visible)throw new Exception("自动军令范围说明挤入建设面板");
        int calls=_humanHistory.Count;
        await PointerClick(_rtsCards["native:castle"]);await HumanIdle();await RtsReady();
        if(_humanHistory.Count!=calls+1)throw new Exception("第一项城堡生产点击未提交");
        // 队列只预付、不占本格本期施工额度；开工等回合末统一执行。
        if(!_humanReceipt.Text.Contains("已排入待建队列")||!_humanReceipt.Text.Contains("预付材料"))throw new Exception("入队回执没有说明预付与待建："+_humanReceipt.Text);
        if(Yes(_rtsData!["ordered"])||_rtsCards["native:castle"].Disabled)throw new Exception("队列模式占用了本格本期施工额度");
        var first=QueuedConstruction().Single()!;
        if(S(first["status"])!="queued"||first["can_start"]?.GetValue<bool>()!=true)throw new Exception("第一条待建没有进入待建队列");
        await PointerClick(_rtsCards["native:castle"]);await HumanIdle();await RtsReady();
        if(_humanHistory.Count!=calls+2)throw new Exception("第二项城堡生产点击未提交");
        var queued=QueuedConstruction();
        if(queued.Count!=2||queued.Any(r=>S(r!["status"])!="queued"))throw new Exception("同格第二项没有排到第一条之后");
        var pending=queued.OrderBy(r=>I(r!["line_position"])).Last()!.DeepClone();
        if(pending["can_start"]?.GetValue<bool>()==true||S(pending["reason"])!="等待本格前面的建设条目")throw new Exception("第二条没有显示等待前一条开工");
        int id=I(pending["id"]);double gold=OwnStock("gold"),wood=OwnStock("wood");
        OpenHumanPage("production");await ToSignal(GetTree().CreateTimer(.2),SceneTreeTimer.SignalName.Timeout);ValidateHumanLayout();
        var cancel=(Button)_humanContent.FindChild("CancelProduction_Build_"+id,true,false)!;
        await PointerClick(cancel);await Screenshot(stem+"-production-cancel.png");await PointerClick(_humanConfirm.GetOkButton());await HumanIdle();
        if(OwnStock("gold")!=gold+F(pending["refunds"]!["native:gold"])||OwnStock("wood")!=wood+F(pending["refunds"]!["native:wood"]))throw new Exception("未开工取消未全额退回预付款");
        if(QueuedConstruction().Count!=1)throw new Exception("取消一条待建后队列条目数不对");
        _humanCell=at;OpenHumanPage("build");await RtsReady();
        await PointerClick(_rtsCards["native:castle"]);await HumanIdle();
        OpenHumanPage("production");await ToSignal(GetTree().CreateTimer(.2),SceneTreeTimer.SignalName.Timeout);
        if(QueuedConstruction().Count!=2)throw new Exception("重新入队后待建条目数不对");
        await Screenshot(stem+"-production-queue.png");
    }
    private async Task ReviewQueueStartsAtTurnEnd(string stem,Vector2I at)
    {
        // 两条待建城堡：回合末只开工第一条并在本期结算落成，第二条继续排队。
        _humanCell=at;OpenHumanPage("build");await RtsReady();
        if(QueuedConstruction().Count!=2)throw new Exception("回合末前待建条目数不对");
        int period=_period;
        await HumanIdle();await PointerClick(_humanNav["end"]);
        await WaitFor(()=>HumanCanWrite&&_period==period+1,"队列开工没有在回合末结算后进入下一期",HumanRoundTimeout);
        var rows=QueuedConstruction();
        if(rows.Count!=1||S(rows[0]!["status"])=="construction")throw new Exception("回合末没有按顺序开工并保留后一条待建："+rows.ToJsonString());
        var siteRow=HumanFacts["building_sites"]!["sites"]!.AsArray().First(s=>I(s!["position"]![0])==at.X&&I(s["position"]![1])==at.Y);
        string siteId=S(siteRow!["id"]);
        if(!HumanFacts["building_sites"]!["instances"]!.AsArray().Any(b=>S(b!["site"])==siteId&&S(b!["kind"])=="native:castle"&&I(b["remaining"])==0))throw new Exception("回合末开工的城堡没有落成");
        var space=OutlinerSpace(at);
        if(space==null||I(space["pending"])!=1||I(space["construction"])!=0)throw new Exception("回合末开工后的空间／状态显示不对");
        await Screenshot(stem+"-queue-started.png");
    }
}
