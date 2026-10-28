using Godot;
using System;
using System.Linq;
using System.Text.Json.Nodes;
using System.Threading.Tasks;
using static WorldMap;

public partial class Desktop
{
    private async Task ReviewProductionQueue(string stem,Vector2I at)
    {
        _humanCell=at;OpenHumanPage("build");await RtsReady();
        if(_targetSummary.Visible)throw new Exception("自动军令范围说明挤入建设面板");
        if(!_productionQueueMode)await PointerClick((CheckButton)_humanContent.FindChild("ProductionQueueMode",true,false)!);
        await RtsReady();
        int calls=_humanHistory.Count;
        await PointerClick(_rtsCards["native:castle"]);await HumanIdle();await RtsReady();
        if(_humanHistory.Count!=calls+1)throw new Exception("第一项城堡生产点击未提交");
        if(!Yes(_rtsData!["ordered"])||_rtsCards["native:castle"].Disabled)throw new Exception("队列模式仍把本期建设额度当作入队门槛");
        await PointerClick(_rtsCards["native:castle"]);await HumanIdle();await RtsReady();
        if(_humanHistory.Count!=calls+2)throw new Exception("第二项城堡生产点击未提交");
        var pending=_humanState!["production_queue"]!["construction"]!.AsArray().Single(r=>S(r!["status"])!="construction")!.DeepClone();
        int id=I(pending["id"]);double gold=OwnStock("gold"),wood=OwnStock("wood");
        OpenHumanPage("production");await ToSignal(GetTree().CreateTimer(.2),SceneTreeTimer.SignalName.Timeout);ValidateHumanLayout();
        var cancel=(Button)_humanContent.FindChild("CancelProduction_Build_"+id,true,false)!;
        await PointerClick(cancel);await Screenshot(stem+"-production-cancel.png");await PointerClick(_humanConfirm.GetOkButton());await HumanIdle();
        if(OwnStock("gold")!=gold+F(pending["refunds"]!["native:gold"])||OwnStock("wood")!=wood+F(pending["refunds"]!["native:wood"]))throw new Exception("未开工取消未全额退回预付款");
        _humanCell=at;OpenHumanPage("build");await RtsReady();
        await PointerClick(_rtsCards["native:castle"]);await HumanIdle();
        OpenHumanPage("production");await ToSignal(GetTree().CreateTimer(.2),SceneTreeTimer.SignalName.Timeout);
        await Screenshot(stem+"-production-queue.png");
    }
}
