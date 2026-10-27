using Godot;
using System;
using System.Collections.Generic;
using System.Linq;
using Zhanguo.Desktop.Layout;

public partial class Desktop
{
    private PanelContainer? _resourceTooltip;
    private readonly Dictionary<string,Control> _resourceHoverRows=new();
    private void HideResourceTooltip()=>_resourceTooltip?.Hide();
    private void BindResourceHover(Control control,NationLedgerView view,LedgerStock stock)
    {
        control.MouseFilter=MouseFilterEnum.Stop;
        control.MouseEntered+=()=>ShowResourceTooltip(view,stock,control.GetGlobalRect());
        control.MouseExited+=HideResourceTooltip;
        _resourceHoverRows.TryAdd(stock.Id,control);
    }
    private void ShowResourceTooltip(NationLedgerView view,LedgerStock stock,Rect2 anchor)
    {
        if(_pauseMenu.Visible)return;
        if(_resourceTooltip==null)
        {
            _resourceTooltip=new PanelContainer { Name="ResourceBreakdown",ZIndex=80,MouseFilter=MouseFilterEnum.Ignore };
            _resourceTooltip.AddThemeStyleboxOverride("panel",HumanArt.Box("panel"));AddChild(_resourceTooltip);
        }
        ClearHumanChildren(_resourceTooltip);
        var box=new VBoxContainer { MouseFilter=MouseFilterEnum.Ignore,CustomMinimumSize=new Vector2(340,0) };box.AddThemeConstantOverride("separation",7);_resourceTooltip.AddChild(box);
        var title=LedgerLabel(stock.Name+" · "+(view.FlowPeriod<0?"尚未结算":$"第 {view.FlowPeriod+1} 期实际收支"),"e8d797");title.AddThemeFontSizeOverride("font_size",18);box.AddChild(title);
        var inventory=LedgerLabel("当前库存 "+NationLedger.Quantity(stock.Amount),"afc3aa");inventory.AddThemeFontSizeOverride("font_size",16);box.AddChild(inventory);
        foreach(var entry in stock.Contributions.OrderByDescending(r=>r.Amount>0))
        {
            var line=new HBoxContainer { MouseFilter=MouseFilterEnum.Ignore };box.AddChild(line);
            if(entry.Kind!=null)line.AddChild(new TextureRect { Texture=RtsArt.Building(entry.Kind),CustomMinimumSize=new Vector2(28,28),ExpandMode=TextureRect.ExpandModeEnum.IgnoreSize,StretchMode=TextureRect.StretchModeEnum.KeepAspectCentered,MouseFilter=MouseFilterEnum.Ignore });
            var label=LedgerLabel((entry.Count>0?entry.Count+" 座":"")+entry.Label);label.AddThemeFontSizeOverride("font_size",16);line.AddChild(label);
            var value=LedgerLabel(NationLedger.Signed(entry.Amount),DeltaColor(entry.Amount),true);value.AddThemeFontSizeOverride("font_size",18);line.AddChild(value);
        }
        if(stock.Contributions.Count==0)box.AddChild(LedgerLabel(view.FlowPeriod<0?"首次结算后显示建筑实际产出与消耗。":"本期没有生产、军需或贡赋收支。","a7b6a1"));
        var net=LedgerLabel("净变化  "+NationLedger.Signed(stock.Change),DeltaColor(stock.Change));net.AddThemeFontSizeOverride("font_size",18);box.AddChild(net);
        double gap=stock.Change-stock.Contributions.Sum(r=>r.Amount);
        if(Math.Abs(gap)>1e-8)box.AddChild(LedgerLabel("明细尚未齐全，差额 "+NationLedger.Signed(gap),"e6b59b"));
        var note=LedgerLabel("按实际运行建筑统计；交易、赠礼另记。","a7b6a1");note.AddThemeFontSizeOverride("font_size",14);box.AddChild(note);
        _resourceTooltip.Show();_resourceTooltip.Size=Vector2.Zero;
        void Position()
        {
            if(_resourceTooltip==null||!_resourceTooltip.Visible)return;
            _resourceTooltip.Size=_resourceTooltip.GetCombinedMinimumSize();
            _resourceTooltip.Position=new Vector2(Math.Clamp(_ledgerPanel.GetGlobalRect().End.X+10,12,Math.Max(12,Size.X-_resourceTooltip.Size.X-12)),
                Math.Clamp(anchor.Position.Y,66,Math.Max(66,Size.Y-_resourceTooltip.Size.Y-76)));
        }
        Position();Callable.From(Position).CallDeferred();
    }
}
