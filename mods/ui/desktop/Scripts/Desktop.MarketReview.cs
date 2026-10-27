using Godot;
using System;
using System.Linq;
using System.Text.Json.Nodes;
using System.Threading.Tasks;
using static WorldMap;
using Zhanguo.Desktop.Layout;

public partial class Desktop
{
    private double OwnStock(string kind)=>F(HumanFacts["econ"]!["stocks"]!.AsArray().First(s=>S(s!["id"])=="native:"+kind)!["amount"]);
    private Button MarketReviewButton(string name)=>(Button)(_humanDrawer.Body.FindChild(name,true,false)??throw new Exception("市场按钮不存在："+name));
    private SpinBox MarketReviewNumber(string name)=>(SpinBox)(_humanContent.FindChild(name,true,false)??throw new Exception("市场数量不存在："+name));
    private CheckButton MarketReviewCheck(string name)=>(CheckButton)(_humanContent.FindChild(name,true,false)??throw new Exception("市场开关不存在："+name));
    private async Task ReviewMarketDemolition(string stem,Vector2I capital,Vector2I other)
    {
        var third=HumanFacts["building_sites"]!["sites"]!.AsArray().Select(s=>new Vector2I(I(s!["position"]![0]),I(s["position"]![1]))).First(p=>p!=capital&&p!=other);
        _humanCell=third;OpenHumanPage("build");await RtsReady();
        await PointerClick(_rtsCards["native:castle"]);await HumanIdle();await RtsReady();
        var pending=_rtsData!["demolitions"]!.AsArray().First(b=>!Yes(b!["completed"]))!;
        int id=I(pending["id"]);double gold=OwnStock("gold"),wood=OwnStock("wood");
        OpenHumanPage("demolish");await WaitFor(()=>_humanContent.FindChild("Demolish_"+id,true,false)!=null,"撤销施工入口没有出现");
        await PointerClick(MarketReviewButton("Demolish_"+id));await Screenshot(stem+"-cancel-confirm.png");
        await PointerClick(_humanConfirm.GetCancelButton());
        if(!HumanFacts["building_sites"]!["instances"]!.AsArray().Any(b=>I(b!["id"])==id))throw new Exception("返回确认框却撤销了施工");
        await PointerClick(MarketReviewButton("Demolish_"+id));await PointerClick(_humanConfirm.GetOkButton());await HumanIdle();
        if(OwnStock("gold")!=gold+F(pending["refunds"]!["native:gold"])||OwnStock("wood")!=wood+F(pending["refunds"]!["native:wood"]))throw new Exception("GUI 撤销返还没有按实际扣款的一半入账");
        if(HumanFacts["building_sites"]!["instances"]!.AsArray().Any(b=>I(b!["id"])==id))throw new Exception("撤销后建筑仍存在");
        OpenHumanPage("build");await RtsReady();
        if(!Yes(_rtsData!["ordered"])||_rtsAvailability.Values.Any(v=>v))throw new Exception("撤销恢复了本格当期施工额度");
        await Screenshot(stem+"-cancelled.png");
        var powerCell=HumanFacts["building_sites"]!["sites"]!.AsArray().Select(s=>new Vector2I(I(s!["position"]![0]),I(s["position"]![1]))).First(p=>p!=capital&&p!=other&&p!=third);
        _humanCell=powerCell;OpenHumanPage("build");await RtsReady();
        await PointerClick(_rtsCards["native:wood-power-plant"]);await HumanIdle();await RtsReady();
        if(NationLedger.Read(_map.Frame!,HumanActor)?.Power?.Generated!=0)throw new Exception("未完工电厂提前计入供电");
        _marketAutomatic=false;OpenHumanPage("market");await MarketReady();
        if(_marketData!["quotes"]!.AsArray().Count!=10)throw new Exception("不是十档正负交易按钮");
        foreach(var side in new[]{"buy","sell"})foreach(int n in new[]{1,10,50,100,500})
            if(_humanContent.FindChild($"Market_{side}_{n}",true,false)==null)throw new Exception("缺少交易数量档位");
        if(!MarketReviewButton("Market_sell_500").Disabled)throw new Exception("超出本国库存的卖单没有变暗");
        double oldFood=OwnStock("food"),cash=OwnStock("gold");
        var quote=_marketData["quotes"]!.AsArray().First(q=>S(q!["side"])=="buy"&&I(q["quantity"])==10)!["quote"]!;
        await HoverRts(MarketReviewButton("Market_buy_10"));
        if(_marketQuote==null||!_marketQuote.Text.Contains("整单均价"))throw new Exception("市场悬停没有整单费用");
        if(OwnStock("food")!=oldFood||OwnStock("gold")!=cash)throw new Exception("悬停报价改变了实际库存");
        await Screenshot(stem+"-market-quote.png");
        await PointerClick(MarketReviewButton("MarketAutoTab"));
        await PointerClick(MarketReviewCheck("AutoTradeBuy"));MarketReviewNumber("AutoBuyTarget").Value=30;MarketReviewNumber("AutoBuyLimit").Value=10;
        await PointerClick(MarketReviewCheck("AutoTradeSell"));MarketReviewNumber("AutoSellTarget").Value=20;
        if(!MarketReviewButton("AutoTradeSave").Disabled)throw new Exception("买卖目标倒挂没有阻止保存");
        MarketReviewNumber("AutoSellTarget").Value=50;
        // A resource roundtrip must preserve an unsaved, account-owned draft.
        await PointerClick(MarketReviewButton("MarketResource_wood"));await MarketReady();
        await PointerClick(MarketReviewButton("MarketResource_food"));await MarketReady();
        if(MarketReviewNumber("AutoBuyTarget").Value!=30||!MarketReviewCheck("AutoTradeBuy").ButtonPressed)throw new Exception("切换商品丢失自动交易草稿");
        await PointerClick(MarketReviewButton("AutoTradeSave"));await HumanIdle();await MarketReady();
        if(!HumanFacts["market"]!["automatic"]!["rules"]!.AsArray().Any(r=>S(r!["resource"])=="native:food"&&I(r["buy_below"])==30))throw new Exception("自动购买设置没有进入本国规则");
        await Screenshot(stem+"-auto-buy.png");
        await PointerClick(MarketReviewButton("MarketResource_wood"));await MarketReady();
        await PointerClick(MarketReviewCheck("AutoTradeSell"));MarketReviewNumber("AutoSellTarget").Value=100;MarketReviewNumber("AutoSellLimit").Value=10;
        await PointerClick(MarketReviewButton("AutoTradeSave"));await HumanIdle();await MarketReady();
        if(!HumanFacts["market"]!["automatic"]!["rules"]!.AsArray().Any(r=>S(r!["resource"])=="native:wood"&&I(r["sell_above"])==100))throw new Exception("自动出售设置没有保存");
        await Screenshot(stem+"-auto-sell.png");
        _humanCell=capital;OpenHumanPage("build");await RtsReady();
    }
    private async Task ReviewCompletedDemolition(string stem,Vector2I at)
    {
        _humanCell=at;OpenHumanPage("demolish");
        var castle=HumanFacts["building_sites"]!["instances"]!.AsArray().First(b=>S(b!["kind"])=="native:castle"&&I(b["remaining"])==0)!;
        int id=I(castle["id"]);double gold=OwnStock("gold"),wood=OwnStock("wood");
        await WaitFor(()=>_humanContent.FindChild("Demolish_"+id,true,false)!=null,"已落成拆除入口不存在");
        await PointerClick(MarketReviewButton("Demolish_"+id));
        if(!_humanConfirm.DialogText.Contains("不返还"))throw new Exception("成品拆除未提前说明零返还");
        await Screenshot(stem+"-demolish-confirm.png");await PointerClick(_humanConfirm.GetOkButton());await HumanIdle();
        if(OwnStock("gold")!=gold||OwnStock("wood")!=wood)throw new Exception("已落成建筑拆除错误返款");
        if(HumanFacts["building_sites"]!["instances"]!.AsArray().Any(b=>I(b!["id"])==id))throw new Exception("已落成建筑没有真正拆除");
    }
}
