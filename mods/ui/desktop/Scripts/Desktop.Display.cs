using Godot;
using System;
using System.IO;
using System.Text.Json;
using System.Text.Json.Nodes;

public partial class Desktop
{
    private string _displayFont=PixelTheme.FontPath;
    private int _ledgerTextSize=20;
    private OptionButton _fontPicker=null!,_ledgerSizePicker=null!;
    private string DisplaySettings=>Path.Combine(_runtime.DataRoot,"archive","desktop-display.json");

    private void BuildDisplayControls(VBoxContainer parent)
    {
        var fonts=new HBoxContainer();fonts.AddChild(new Label { Text="字体",CustomMinimumSize=new Vector2(72,0) });
        _fontPicker=new OptionButton { SizeFlagsHorizontal=SizeFlags.ExpandFill };
        _fontPicker.AddItem("Z 工坊像素圆体 16px");_fontPicker.AddItem("原 Fusion Pixel 12px");fonts.AddChild(_fontPicker);parent.AddChild(fonts);
        _fontPicker.ItemSelected+=index=>{_displayFont=index==0?PixelTheme.FontPath:PixelTheme.LegacyFontPath;ApplyDisplayOptions(true);};
        var sizes=new HBoxContainer();sizes.AddChild(new Label { Text="账目字号",CustomMinimumSize=new Vector2(72,0) });
        _ledgerSizePicker=new OptionButton { SizeFlagsHorizontal=SizeFlags.ExpandFill };
        _ledgerSizePicker.AddItem("标准 · 16px");_ledgerSizePicker.AddItem("大字 · 20px");sizes.AddChild(_ledgerSizePicker);parent.AddChild(sizes);
        _ledgerSizePicker.ItemSelected+=index=>{_ledgerTextSize=index==0?16:20;ApplyDisplayOptions(true);};
        _rightReturnToggle=new CheckButton { Name="RightClickReturn",Text="右键返回上次操作" };
        _rightReturnToggle.AddThemeFontSizeOverride("font_size",14);
        _rightReturnToggle.TooltipText="选军时优先下达军令；无选军且不在编辑路径时，右键回到上次操作页／按钮，不自动执行。关闭时右键拖动地图。";
        parent.AddChild(_rightReturnToggle);_rightReturnToggle.Toggled+=on=>{_rightClickReturn=on;ApplyDisplayOptions(true);};
    }
    private void LoadDisplayOptions()
    {
        try
        {
            if(File.Exists(DisplaySettings)&&JsonNode.Parse(File.ReadAllText(DisplaySettings)) is JsonObject saved)
            {
                if(saved["font"]?.ToString()=="fusion")_displayFont=PixelTheme.LegacyFontPath;
                if(saved["ledger_font_size"]?.GetValue<int>() is 16 or 20)_ledgerTextSize=saved["ledger_font_size"]!.GetValue<int>();
                if(saved["right_click_return"] is JsonValue right)_rightClickReturn=right.GetValue<bool>();
                _simpleTerrain=saved["map_mode"]?.ToString()=="terrain";
            }
        }
        catch(Exception e){GD.PrintErr("显示设置使用默认值："+e.Message);}
        ApplyDisplayOptions(false);
    }
    private void ApplyDisplayOptions(bool save)
    {
        var textTheme=PixelTheme.Create(_ledgerTextSize,_displayFont);
        Theme.DefaultFont=textTheme.DefaultFont;
        _map.SetDisplayFont(textTheme.DefaultFont);
        _ledgerPanel.Theme=textTheme;_settlementCard.Theme=textTheme;
        _fontPicker.Selected=_displayFont==PixelTheme.FontPath?0:1;_ledgerSizePicker.Selected=_ledgerTextSize==16?0:1;
        ApplyMapPreferences();
        if(_map.Frame!=null)RefreshNationLedger();LayoutLedger();
        if(!save)return;
        try
        {
            Directory.CreateDirectory(Path.GetDirectoryName(DisplaySettings)!);
            File.WriteAllText(DisplaySettings,JsonSerializer.Serialize(new { font=_displayFont==PixelTheme.FontPath?"zlabs":"fusion",ledger_font_size=_ledgerTextSize,
                right_click_return=_rightClickReturn,map_mode=_simpleTerrain?"terrain":"normal" }));
        }
        catch(Exception e){GD.PrintErr("显示设置未保存："+e.Message);}
    }
}
