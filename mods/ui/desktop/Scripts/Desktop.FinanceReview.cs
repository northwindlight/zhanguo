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
    private async Task ReviewHumanBankLoan(string stem)
    {
        OpenHumanPage("bank");await WaitFor(()=>_bankAvailable,"正常结算后银行授信未出现");
        double cash=OwnStock("gold"),credit=F(HumanFacts["bank"]!["credit_limit"]);
        double fee=0;
        HumanQuery("panel",new JsonObject { ["panel"]="quote",["action"]="borrow" },r=>fee=F(r["data"]?["fee"]));
        await WaitFor(()=>_humanQueries.Count==0,"银行报价未返回");
        await PointerClick(_bankBorrow!);await PointerClick(_humanConfirm.GetCancelButton());
        if(OwnStock("gold")!=cash||HumanFacts["bank"]!["loan"]!=null)throw new Exception("返回贷款确认框仍发生借款");
        await PointerClick(_bankBorrow!);await Screenshot(stem+"-bank-confirm.png");
        await PointerClick(_humanConfirm.GetOkButton());await HumanIdle();
        await WaitFor(()=>_bankBorrow!=null&&_bankBorrow.Disabled,"已有贷款却仍可借款");
        var loan=HumanFacts["bank"]!["loan"];
        if(loan==null||F(loan["principal"])!=credit||OwnStock("gold")!=cash+credit-fee)throw new Exception("银行贷款与正式授信／实付费用不一致");
        await Screenshot(stem+"-bank-loan.png");
    }
    private async Task ReviewFinanceMailbox(string path)
    {
        try
        {
            string stem=Path.ChangeExtension(path,null);
            var input=JsonNode.Parse(File.ReadAllText(OS.GetEnvironment("ZHANGUO_FINANCE_REVIEW")))!;
            var frames=input["frames"]!.AsArray().ToDictionary(s=>S(s!["name"]),s=>s!["frame"]!);
            var directory=stem+"-mail-state";Directory.CreateDirectory(directory);
            string save=Path.Combine(directory,Guid.NewGuid().ToString("N")+".session");
            _humanStarted=true;_humanPage="intel";_savePath=save;
            void Apply(string name)
            {
                ApplyFrame(frames[name].DeepClone().AsObject());
                _status.Text=$"第 {_period+1} 回合 · 离线报表验收";
                _bannerTitle.Text=$"{NationLabel(HumanActor)} · 第 {_period+1} 回合";_bannerText.Text="正式经济报表 · 只读邮箱";
            }
            Apply("9");if(UnreadReports!=0||_reportNotice.Visible)throw new Exception("第10期前提前投递报表");
            Apply("10");if(UnreadReports!=1||!_reportNotice.Visible)throw new Exception("正式报表没有自动未读提醒");
            string original=_humanState!.ToJsonString();
            await PointerClick(_reportNotice);await WaitFor(()=>_humanContent.FindChild("EconomicReportBody",true,false)!=null,"提醒没有打开报表");
            string report=((Label)_humanContent.FindChild("EconomicReportBody",true,false)).Text;
            foreach(string key in new[]{"第 1—10 期","征兵","进口","出口","负债","库存资产","建筑资产","GDP增长","资产增长","暂无可比值"})
                if(!report.Contains(key))throw new Exception("完整报表缺少字段："+key);
            if(UnreadReports!=0||_reportNotice.Visible||_mailReportTab.Text!="经济报表"||original!=_humanState.ToJsonString())throw new Exception("读报表改变游戏事实或没有清除未读");
            await Screenshot(stem+"-report.png");
            _humanScroll.ScrollVertical=100000;await ToSignal(GetTree(),SceneTree.SignalName.ProcessFrame);
            if(!new Rect2(Vector2.Zero,Size).Encloses(_mailBack.GetGlobalRect()))throw new Exception("阅读长报表后返回栏被滚走");
            await PointerClick(_mailBack);
            Apply("20");if(UnreadReports!=1||MailReports.Count()!=2)throw new Exception("第二份报表覆盖了历史或重复提醒旧报表");
            await Screenshot(stem+"-report-inbox.png");
            await PointerClick(_reportNotice);await PointerClick(_mailBack);
            _mailSave="";_readReports.Clear();Apply("20");
            if(UnreadReports!=0)throw new Exception("重新读取同一存档丢失已读标记");
            _savePath=save+"-other";Apply("20");if(UnreadReports!=2)throw new Exception("不同游戏串用了邮箱已读状态");
            _savePath=save;Apply("20");if(MailReports.Any(r=>S(r["owner"])!=HumanActor))throw new Exception("未购买就显示他国报表");
            Apply("purchased");if(UnreadReports!=1||MailReports.Count()!=3)throw new Exception("已购买的历史报表没有进入邮箱");
            var layouts=new JsonArray();
            foreach(var size in new[]{new Vector2I(1920,1080),new Vector2I(1440,900),new Vector2I(1280,720),new Vector2I(1024,768)})
            {
                GetWindow().ContentScaleSize=size;GetWindow().Size=size;
                _savePath=Path.Combine(directory,Guid.NewGuid().ToString("N")+".session");_mailReport="";Apply("10");
                _map.Fit();await ToSignal(GetTree().CreateTimer(.2),SceneTreeTimer.SignalName.Timeout);LayoutReportNotice();
                var viewport=new Rect2(Vector2.Zero,Size);
                if(!viewport.Encloses(_reportNotice.GetGlobalRect())||_reportNotice.GetGlobalRect().Intersects(_ledgerPanel.GetGlobalRect()))throw new Exception("报表提醒越界或遮住账目");
                await Screenshot(stem+$"-notice-{size.X}.png");
                await PointerClick(_reportNotice);ValidateHumanLayout();
                if(!viewport.Encloses(_mailTools.GetGlobalRect()))throw new Exception("邮箱固定工具栏越界");
                _humanScroll.ScrollVertical=100000;await ToSignal(GetTree(),SceneTree.SignalName.ProcessFrame);
                if(!viewport.Encloses(_mailBack.GetGlobalRect()))throw new Exception("窄窗返回按钮不可见");
                await Screenshot(stem+$"-report-{size.X}.png");await PointerClick(_mailBack);
                layouts.Add(new JsonObject { ["width"]=size.X,["height"]=size.Y,["within_viewport"]=true });
            }
            var result=new JsonObject { ["report_delivery"]=true,["report_periods"]=true,["report_history"]=true,
                ["report_read_persistence"]=true,["report_game_isolation"]=true,["report_purchase_permissions"]=true,["report_fixed_return"]=true,["layouts"]=layouts,
                ["platform"]=OS.GetName(),["renderer"]=RenderingServer.GetVideoAdapterName() };
            File.WriteAllText(path+".json",result.ToJsonString(new JsonSerializerOptions { WriteIndented=true }));
            GD.Print("FINANCE_MAILBOX_GUI_OK "+path);GetTree().Quit();
        }
        catch(Exception e){GD.PrintErr(e);GetTree().Quit(3);}
    }
}
