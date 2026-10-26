using Godot;
using System;
using System.Collections.Generic;
using System.Linq;
using System.Text.Json.Nodes;
using Zhanguo.Desktop.Layout;
using static WorldMap;

public partial class Desktop
{
    private PanelContainer _ledgerPanel=null!,_settlementCard=null!;
    private Label _ledgerHeading=null!,_ledgerPeriod=null!,_settlementHeading=null!,_settlementInfo=null!,_settlementSupply=null!,_settlementTribute=null!,_settlementEmpty=null!;
    private VBoxContainer _settlementBody=null!;
    private GridContainer _ledgerStocks=null!,_settlementStocks=null!;
    private string _ledgerActor="";
    private readonly List<string> _ledgerSwitches=new();
    private int _nationalSettlements;

    private static PanelContainer LedgerCard(Control parent,int z)
    {
        var panel=new PanelContainer { ZIndex=z,MouseFilter=MouseFilterEnum.Stop,Visible=false };
        var style=new StyleBoxFlat { BgColor=new Color(.045f,.08f,.08f,.96f),BorderColor=new Color("63766a"),
            ContentMarginLeft=14,ContentMarginRight=14,ContentMarginTop=12,ContentMarginBottom=12 };
        style.SetBorderWidthAll(1);panel.AddThemeStyleboxOverride("panel",style);parent.AddChild(panel);return panel;
    }
    private static Label LedgerLabel(string text,string color="e7e9db",bool right=false)
    {
        var label=new Label { Text=text,MouseFilter=MouseFilterEnum.Ignore,SizeFlagsHorizontal=SizeFlags.ExpandFill,
            HorizontalAlignment=right?HorizontalAlignment.Right:HorizontalAlignment.Left };
        label.AddThemeColorOverride("font_color",new Color(color));return label;
    }
    private static void ClearLedgerRows(GridContainer grid)
    {
        foreach(Node child in grid.GetChildren()){grid.RemoveChild(child);child.QueueFree();}
    }
    private static string DeltaColor(double value)=>value>0?"a2e4a6":value<0?"ff9891":"91a49b";

    private void BuildNationLedger()
    {
        _ledgerPanel=LedgerCard(this,6);var box=new VBoxContainer();box.AddThemeConstantOverride("separation",7);_ledgerPanel.AddChild(box);
        _ledgerHeading=LedgerLabel("列国收支");_ledgerHeading.TextOverrunBehavior=TextServer.OverrunBehavior.TrimEllipsis;box.AddChild(_ledgerHeading);
        _ledgerPeriod=LedgerLabel("","a5b8ac");_ledgerPeriod.TextOverrunBehavior=TextServer.OverrunBehavior.TrimEllipsis;box.AddChild(_ledgerPeriod);
        _ledgerStocks=new GridContainer { Columns=3 };_ledgerStocks.AddThemeConstantOverride("h_separation",10);
        _ledgerStocks.AddThemeConstantOverride("v_separation",4);box.AddChild(_ledgerStocks);
        var note=LedgerLabel("生产／军耗／贡赋净变化","a5b8ac");box.AddChild(note);
        _ledgerPanel.TooltipText="当前库存来自已展示的实际行动。变化为标注期的生产、军队消耗和贡赋净值；不含交易与赠礼。绿色增加，红色减少。";

        _settlementCard=LedgerCard(this,9);
        var scroll=new ScrollContainer { HorizontalScrollMode=ScrollContainer.ScrollMode.Disabled,SizeFlagsVertical=SizeFlags.ExpandFill };
        _settlementCard.AddChild(scroll);
        var summary=new VBoxContainer { SizeFlagsHorizontal=SizeFlags.ExpandFill };_settlementBody=summary;
        summary.AddThemeConstantOverride("separation",10);scroll.AddChild(summary);
        _settlementHeading=LedgerLabel("");summary.AddChild(_settlementHeading);
        _settlementInfo=LedgerLabel("","a5b8ac");summary.AddChild(_settlementInfo);
        _settlementEmpty=LedgerLabel("本期未记录生产或军需支出","a5b8ac");_settlementEmpty.AutowrapMode=TextServer.AutowrapMode.WordSmart;summary.AddChild(_settlementEmpty);
        _settlementStocks=new GridContainer { Columns=4 };_settlementStocks.AddThemeConstantOverride("h_separation",18);
        _settlementStocks.AddThemeConstantOverride("v_separation",7);summary.AddChild(_settlementStocks);
        _settlementTribute=LedgerLabel("","ddd7b6");_settlementTribute.AutowrapMode=TextServer.AutowrapMode.WordSmart;summary.AddChild(_settlementTribute);
        _settlementSupply=LedgerLabel("");_settlementSupply.AutowrapMode=TextServer.AutowrapMode.WordSmart;summary.AddChild(_settlementSupply);
        LoadDisplayOptions();
    }
    private void LayoutLedger()
    {
        if(_ledgerPanel==null)return;
        _ledgerPanel.Position=new Vector2(16,78);_ledgerPanel.Size=new Vector2(280,0);
        float width=Math.Min(520,Size.X-328);
        float height=Math.Min(_settlementBody.GetCombinedMinimumSize().Y+24,Math.Max(180,Size.Y-298));
        _settlementCard.Position=new Vector2(Size.X-width-16,220);_settlementCard.Size=new Vector2(width,height);
        if(_banner!=null)
        {
            // Leave the whole country panel clear, including at 1024px.
            float left=312,right=Size.X-16;
            _banner.SetAnchorsAndOffsetsPreset(LayoutPreset.TopLeft);
            _banner.Position=new Vector2(left,78);_banner.Size=new Vector2(Math.Min(700,right-left),126);
        }
    }
    private void RefreshNationLedger(string? actor=null)
    {
        if(_map.Frame==null)return;
        string target=actor??_ledgerActor;
        var view=NationLedger.Read(_map.Frame,target);
        if(view==null)
        {
            target=_map.Frame["nations"]?.AsArray().FirstOrDefault()?["id"]?.ToString()??"";
            view=NationLedger.Read(_map.Frame,target);
        }
        if(view==null){_ledgerPanel.Visible=false;return;}
        if(target!=_ledgerActor){_ledgerActor=target;if(_smoke!=null)_ledgerSwitches.Add(target);}
        _ledgerHeading.Text=$"{view.Name}国 · 第 {view.Period+1} 回合";
        _ledgerHeading.Modulate=_map.NationColor(target);
        _ledgerPeriod.Text="当前库存 · "+(view.FlowPeriod<0?"尚未结算":$"第 {view.FlowPeriod+1} 期净变化");
        ClearLedgerRows(_ledgerStocks);
        foreach(var row in view.Stocks)
        {
            _ledgerStocks.AddChild(LedgerLabel(row.Name));
            var amount=LedgerLabel(NationLedger.Quantity(row.Amount),right:true);amount.TooltipText=row.Amount.ToString("G17");
            amount.TextOverrunBehavior=TextServer.OverrunBehavior.TrimEllipsis;_ledgerStocks.AddChild(amount);
            var change=LedgerLabel(view.FlowPeriod<0?"—":NationLedger.Signed(row.Change),DeltaColor(row.Change),true);
            change.TextOverrunBehavior=TextServer.OverrunBehavior.TrimEllipsis;change.TooltipText=row.Change.ToString("G17");_ledgerStocks.AddChild(change);
        }
        _ledgerPanel.Visible=true;LayoutLedger();
    }
    private void ShowNationalSettlement(JsonObject beat)
    {
        _settlementCard.Visible=false;
        string actor=S(beat["actor"]);
        if(actor==""&&beat["combat"] is JsonObject combat)
            actor=combat["approaches"]?.AsArray().FirstOrDefault()?["actor"]?.ToString()??combat["before"]?.AsArray().FirstOrDefault(u=>S(u!["owner"])!="野人"&&S(u["owner"])!=S(combat["previous_owner"]))?["owner"]?.ToString()??"";
        if(actor!="")RefreshNationLedger(actor);else RefreshNationLedger();
        if(beat["ledger"] is not JsonObject ledger)return;
        _nationalSettlements++;_bubbles.HideAll();
        bool production=S(ledger["stage"])=="production";
        _settlementHeading.Text=$"{_map.NationName(actor)} · {(production?"采集与生产":"补给与恢复")}";
        _settlementHeading.Modulate=_map.NationColor(actor);
        _settlementInfo.Text=$"第 {_timelinePeriod+1} 期 · "+(production?$"{I(ledger["facilities"])} 处设施有实际收支":$"{I(ledger["units"])} 支军队统一供给");
        ClearLedgerRows(_settlementStocks);
        _settlementStocks.Columns=production?3:2;
        foreach(var caption in production?new[]{"资源","产出","用料"}:new[]{"资源","军需耗用"})_settlementStocks.AddChild(LedgerLabel(caption,"a5b8ac",caption!="资源"));
        var income=NationLedger.Amounts(ledger["production_in"]);var input=NationLedger.Amounts(ledger["production_out"]);
        var military=NationLedger.Amounts(ledger["military_out"]);
        var stocks=NationLedger.Read(_map.Frame!,actor)?.Stocks??Array.Empty<LedgerStock>();
        foreach(var row in stocks.Where(r=>income.ContainsKey(r.Id)||input.ContainsKey(r.Id)||military.ContainsKey(r.Id)))
        {
            _settlementStocks.AddChild(LedgerLabel(row.Name));
            foreach(double value in production?new[]{income.GetValueOrDefault(row.Id),-input.GetValueOrDefault(row.Id)}:new[]{-military.GetValueOrDefault(row.Id)})
                _settlementStocks.AddChild(LedgerLabel(NationLedger.Signed(value),DeltaColor(value),true));
        }
        _settlementEmpty.Visible=_settlementStocks.GetChildCount()==_settlementStocks.Columns;_settlementStocks.Visible=!_settlementEmpty.Visible;
        string Flows(string key,string sign)=>string.Join("  ",NationLedger.Amounts(ledger[key]).Select(p=>$"{stocks.FirstOrDefault(r=>r.Id==p.Key)?.Name??p.Key} {sign}{NationLedger.Quantity(p.Value)}"));
        string received=Flows("tribute_in","+"),paid=Flows("tribute_out","-");
        _settlementTribute.Text=string.Join("\n",new[]{received==""?"":"受贡  "+received,paid==""?"":"上贡  "+paid}.Where(s=>s!=""));
        _settlementTribute.Visible=_settlementTribute.Text!="";
        double need=F(ledger["supply_need"]),used=F(ledger["supply_used"]),healed=F(ledger["healed_hp"]),loss=F(ledger["starvation_hp"]);
        _settlementSupply.Text=$"补给实耗 {used:g} / 需求 {need:g}"+(need>used?$" · 缺 {need-used:g}":"")+
            (healed>0?$"\n恢复 +{healed:g} HP":"")+(loss>0?$"\n断粮损失 -{loss:g} HP":"");
        _settlementSupply.Modulate=new Color(need>used?"ff9891":"b8d5b3");
        _settlementSupply.Visible=!production;
        _settlementCard.Visible=true;LayoutLedger();Callable.From(LayoutLedger).CallDeferred();
    }
    private void ValidateLedgerLayout()
    {
        var viewport=new Rect2(Vector2.Zero,Size);
        if(!viewport.Encloses(_ledgerPanel.GetGlobalRect()))throw new Exception("单国收支面板超出窗口");
        if(_ledgerPanel.GetGlobalRect().Intersects(_banner.GetGlobalRect()))throw new Exception("国家面板与横幅重叠");
        if(_settlementCard.Visible&&(!viewport.Encloses(_settlementCard.GetGlobalRect())||
            _ledgerPanel.GetGlobalRect().Intersects(_settlementCard.GetGlobalRect())||_banner.GetGlobalRect().Intersects(_settlementCard.GetGlobalRect())))
            throw new Exception("全国结算卡片越界或重叠");
        foreach(Control row in _ledgerStocks.GetChildren())
            if(!_ledgerPanel.GetGlobalRect().Encloses(row.GetGlobalRect()))throw new Exception("国家账目行超出面板");
    }
}
