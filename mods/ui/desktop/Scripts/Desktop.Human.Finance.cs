using Godot;
using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text.Json;
using System.Text.Json.Nodes;
using static WorldMap;

public partial class Desktop
{
    private Button? _bankBorrow;
    private bool _bankAvailable;
    private void RenderHumanBank()
    {
        var bank=HumanFacts["bank"]!;
        HumanText($"可用金币  {Quantity(bank["cash"])}\n当前债务  {Quantity(bank["liabilities"])}",true).Name="BankBalance";
        HumanText($"存款利率 {F(bank["savings_rate"]):P1} · 当前贷款利率 {F(bank["loan_rate"]):P1}");
        HumanText($"最近已结期 GDP  {(bank["gdp"]==null?"尚无":Quantity(bank["gdp"]))}\n授信倍数 {Quantity(bank["credit_multiple"])} · 贷款期限 {bank["loan_term"]} 回合");
        HumanText("贷款一次借满正式授信，最多同时一笔；利息按每期实际利率复利计入债务，到期统一扣款。借款不计入GDP。",color:"adbea7");
        var loan=bank["loan"];
        if(loan!=null)HumanText($"未清贷款\n本金 {Quantity(loan["principal"])} 金 · 当前应还 {Quantity(loan["due"])} 金\n剩余 {loan["remaining"]} 回合 · 借入时利率 {F(loan["initial_rate"]):P1}",true).Name="BankLoan";
        var preview=HumanText("正在读取正式授信与手续费…",color:"dfd298");preview.Name="BankQuote";
        _bankAvailable=false;
        _marketFooter.Show();
        var borrow=Button(_marketFooter,"贷款 · 等待报价",()=>
        {
            if(!_bankAvailable||!HumanCanWrite)return;
            ConfirmHuman("确认银行贷款",preview.Text+"\n确认后按提交时的正式授信借款；到期自动扣款。",()=>SubmitHuman("borrow",new JsonObject()));
        });HumanArt.Style(borrow);borrow.Icon=MapIcons.Texture("bank");borrow.SizeFlagsHorizontal=SizeFlags.ExpandFill;borrow.Name="BankBorrow";_bankBorrow=borrow;borrow.Disabled=true;
        int epoch=_rtsEpoch;
        HumanQuery("panel",new JsonObject { ["panel"]="quote",["action"]="borrow" },row=>
        {
            if(_humanPage is not ("bank" or "finance")||epoch!=_rtsEpoch||!IsInstanceValid(borrow))return;
            var q=row["data"]!;
            _bankAvailable=Yes(q["can_borrow"])&&HasHumanOperation("borrow");
            string reason=q["loan"]!=null?"已有未清贷款":F(q["credit"])<=0?"尚无可用授信，需先完成产生GDP的结算":F(q["cash"])<F(q["fee"])?"金币不足以支付手续费":"当前贷款操作不可用";
            preview.Text=$"正式授信 {Quantity(q["credit"])} 金 · 手续费 {Quantity(q["fee"])} 金\n"+
                $"期限 {q["loan_term"]} 回合 · 当前利率 {F(q["loan_rate"]):P1}\n"+(_bankAvailable?"可以贷款，最终以实际回执为准。":reason);
            borrow.Text=_bankAvailable?$"借入 {Quantity(q["credit"])} 金":"当前不可贷款";borrow.TooltipText=preview.Text;RefreshHumanButtons();
        });
        HumanButton("经济报表邮箱",()=>{_mailReports=true;_mailReport="";OpenHumanPage("intel");},"intelligence");
    }

    private readonly HashSet<string> _readReports=new();
    private string _mailSave="",_mailReport="";
    private bool _mailReports=true;
    private HBoxContainer _mailTools=null!;
    private Button _mailBack=null!,_mailReportTab=null!;
    private Button _reportNotice=null!;
    private IEnumerable<JsonNode> MailReports=>
        (_humanState?["reports"]?.AsArray()??new JsonArray()).Select(r=>r!)
        .Concat((HumanFacts["bank"]?["purchases"]?.AsArray()??new JsonArray()).Select(p=>p!["report"]!))
        .GroupBy(ReportKey).Select(g=>g.First()).OrderByDescending(r=>I(r["end"])).ThenBy(r=>S(r["owner"]));
    private static string ReportKey(JsonNode r)=>S(r["owner"])+"/"+S(r["end"]);
    private static string ReportRange(JsonNode r)=>$"第 {I(r["start"])+1}—{I(r["end"])+1} 期";
    private int UnreadReports=>MailReports.Count(r=>!_readReports.Contains(ReportKey(r)));
    private void BuildReportNotice()
    {
        _reportNotice=new Button { Name="ReportNotice",ZIndex=11,Visible=false };
        _reportNotice.AddThemeFontSizeOverride("font_size",14);HumanArt.Style(_reportNotice,"intelligence");AddChild(_reportNotice);
        _reportNotice.Icon=MapIcons.Texture("mail");
        _reportNotice.Pressed+=()=>
        {
            var report=MailReports.FirstOrDefault(r=>!_readReports.Contains(ReportKey(r)));
            _mailReports=true;_mailReport=report==null?"":ReportKey(report);OpenHumanPage("intel");
        };
    }
    private void BuildMailboxToolbar(VBoxContainer parent)
    {
        _mailTools=new HBoxContainer { Visible=false };parent.AddChild(_mailTools);
        _mailBack=Button(_mailTools,"← 列表",()=>{_mailReport="";RenderHumanPage();});_mailBack.Name="MailBack";
        _mailReportTab=Button(_mailTools,"经济报表",()=>{_mailReports=true;_mailReport="";RenderHumanPage();});
        Button(_mailTools,"来信情报",()=>{_mailReports=false;_mailReport="";RenderHumanPage();});
        Button(_mailTools,"情报操作",()=>OpenHumanPage("intel-actions")).Name="MailboxActions";
        foreach(var button in _mailTools.GetChildren().OfType<Button>())
        {HumanArt.Style(button);button.AddThemeFontSizeOverride("font_size",14);button.SizeFlagsHorizontal=SizeFlags.ExpandFill;}
    }
    private void LayoutReportNotice()
    {
        if(_reportNotice==null)return;
        _reportNotice.Visible=HumanMode&&!_pauseMenu.Visible&&UnreadReports>0;
        _reportNotice.Size=new Vector2(280,52);
        _reportNotice.Position=new Vector2(16,Math.Min(Size.Y-226,(_ledgerPanel?.GetGlobalRect().End.Y??400)+10));
    }
    private void RefreshMailbox()
    {
        if(!HumanMode||_reportNotice==null)return;
        if(_savePath!=""&&_mailSave!=_savePath)
        {
            _mailSave=_savePath;_readReports.Clear();
            try
            {
                if(File.Exists(_mailSave+".desktop-mail.json")&&JsonNode.Parse(File.ReadAllText(_mailSave+".desktop-mail.json")) is JsonArray read)
                    foreach(var key in read)_readReports.Add(S(key));
            }
            catch(Exception e){GD.PrintErr("邮箱已读记录使用默认值："+e.Message);}
        }
        int unread=UnreadReports,letters=HumanFacts["intel"]?.AsArray().Count(m=>!_readIntel.Contains(I(m!["id"])))??0;
        _humanNav["intel"].Text="邮箱"+Badge(unread+letters);
        _mailReportTab.Text="经济报表"+Badge(unread);
        var latest=MailReports.FirstOrDefault(r=>!_readReports.Contains(ReportKey(r)));
        _reportNotice.Text=latest==null?"":$"新经济报表 ({unread}) · {ReportRange(latest)}\n点击阅读 · 与上期资源收支分别统计";
        _reportNotice.TooltipText="只显示本国免费报表及已经购买的他国报表。发布期与覆盖期以正式报表为准。";
        LayoutReportNotice();
    }
    private void ReadReport(JsonNode report)
    {
        if(!_readReports.Add(ReportKey(report)))return;
        try
        {
            if(_mailSave!="")File.WriteAllText(_mailSave+".desktop-mail.json",JsonSerializer.Serialize(_readReports.OrderBy(k=>k)));
        }
        catch(Exception e){GD.PrintErr("邮箱已读记录未保存："+e.Message);}
        RefreshMailbox();
    }
    private void RenderHumanMailbox()
    {
        _mailBack.Visible=_mailReport!="";_mailReportTab.Text="经济报表"+Badge(UnreadReports);
        if(!_mailReports){RenderHumanIntel();return;}
        var opened=MailReports.FirstOrDefault(r=>ReportKey(r)==_mailReport);
        if(opened!=null)
        {
            ReadReport(opened);
            HumanText(S(opened["owner"])==HumanActor?"本国报表 · 免费投递":"已购买的他国报表 · 历史情报",true);
            HumanText(EconomicReport(opened)).Name="EconomicReportBody";return;
        }
        HumanText("正式报表每十期发布，汇总覆盖期；左上角资源面板展示当期库存和上一期收支，两者时间范围不同。",color:"b0c4aa");
        if(!MailReports.Any())HumanText("尚无已发布的经济报表。首份本国报表覆盖第1—10期，发布后免费投递并提醒。");
        foreach(var report in MailReports)
        {
            string key=ReportKey(report);bool unread=!_readReports.Contains(key);
            var button=HumanButton($"{(unread?"● 未读 · ":"")}{NationLabel(S(report["owner"]))} · {ReportRange(report)}\nGDP {Money(report["gdp"])} · 净资产 {Money(report["net_assets"])}",()=>{_mailReport=key;RenderHumanPage();},"intelligence");
            button.Name="Report_"+I(report["end"]);button.Alignment=HorizontalAlignment.Left;button.AddThemeFontSizeOverride("font_size",15);
            button.TooltipText="打开完整已发布报表；读取免费，不购买新情报。";
        }
    }
    private string ReportMoney(JsonNode? value)
    {
        if(value is not JsonObject m)return Money(value);
        if(m["total"]!=null)return Quantity(m["total"]);
        return $"已估值小计 {Quantity(m["known"])}；未估值："+string.Join("、",m["unpriced"]!.AsArray().Select(v=>HumanLabel(S(v))));
    }
    private static string Ratio(JsonNode? value)=>value==null?"暂无可比值":F(value).ToString("P1",System.Globalization.CultureInfo.InvariantCulture);
    private string EconomicReport(JsonNode r)=>
        $"{NationLabel(S(r["owner"]))} · {ReportRange(r)} · 共 {r["span"]} 期\n\n"+
        $"经济活动（覆盖期累计）\nGDP {ReportMoney(r["gdp"])}\n建设投资 {ReportMoney(r["investment"])}\n征兵 {ReportMoney(r["recruitment"])}\n军需消耗 {ReportMoney(r["military"])}\n进口 {Quantity(r["imports"])} · 出口 {Quantity(r["exports"])}\n贸易比率 {Ratio(r["trade_ratio"])}\n贡赋支出 {ReportMoney(r["tribute_paid"])} · 收入 {ReportMoney(r["tribute_received"])}\n\n"+
        $"期末资产与负债\n库存资产 {ReportMoney(r["inventory_assets"])}\n建筑资产 {ReportMoney(r["building_assets"])}\n总资产 {ReportMoney(r["total_assets"])}\n负债 {Quantity(r["liabilities"])}\n净资产 {ReportMoney(r["net_assets"])}\n\n"+
        $"相对上一份报表\nGDP增长 {Ratio(r["gdp_growth"])}\n投资增长 {Ratio(r["investment_growth"])}\n资产增长 {Ratio(r["assets_growth"])}\n军费／GDP {Ratio(r["military_ratio"])}\n\nGDP不是金币实收；军需折价不是额外扣款。未估值与缺少可比值不会按零显示。";
}
