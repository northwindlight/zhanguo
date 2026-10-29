using Godot;
using System;
using System.Collections.Generic;
using System.Linq;
using System.Text.Json.Nodes;
using static WorldMap;
using Zhanguo.Desktop.Layout;

public partial class Desktop
{
    private void RenderMarketOverview(JsonObject frame)
    {
        ClearHumanChildren(_market);
        foreach(var row in frame["market"]!.AsArray())
        {
            var panel=new PanelContainer();panel.AddThemeStyleboxOverride("panel",HumanArt.Box("panel"));_market.AddChild(panel);
            var line=new HBoxContainer();line.AddThemeConstantOverride("separation",14);panel.AddChild(line);
            line.AddChild(new TextureRect { Texture=RtsArt.Resource(S(row!["id"])),CustomMinimumSize=new Vector2(42,42),ExpandMode=TextureRect.ExpandModeEnum.IgnoreSize,StretchMode=TextureRect.StretchModeEnum.KeepAspectCentered });
            var info=new VBoxContainer { SizeFlagsHorizontal=SizeFlags.ExpandFill };line.AddChild(info);
            var name=new Label { Text=$"{row["name"]}\n市场库存 {Quantity(row["inventory"])} / 期望 {Quantity(row["reference"])}",AutowrapMode=TextServer.AutowrapMode.WordSmart,Modulate=new Color("e6d9ac") };name.AddThemeFontSizeOverride("font_size",18);info.AddChild(name);
            var price=new Label { Text=$"买 {DesktopScene.PriceText(row["buy"]==null?null:F(row["buy"]))}   卖 {DesktopScene.PriceText(row["sell"]==null?null:F(row["sell"]))}",AutowrapMode=TextServer.AutowrapMode.WordSmart,Modulate=new Color("b8cdb3") };price.AddThemeFontSizeOverride("font_size",16);info.AddChild(price);
        }
        _market.AddChild(new Label { Text=$"储蓄 {F(frame["bank"]!["rate"]):P1} · 贷款 {F(frame["bank"]!["loan_rate"]):P1}\n边际单价仅供参考，大单均价随数量变化。",AutowrapMode=TextServer.AutowrapMode.WordSmart,Modulate=new Color("b2bca5") });
    }
    private string _marketResource="native:food";
    private int _marketEpoch;
    private bool _marketAutomatic,_marketAdvanced;
    private JsonObject? _marketData;
    private Label? _marketQuote;
    private HBoxContainer _marketFooter=null!;
    private readonly Dictionary<Button,bool> _marketAvailability=new();
    private readonly Dictionary<string,JsonObject> _marketDrafts=new();
    private Func<JsonObject>? _readMarketDraft;
    private bool _marketDraftDirty;

    private void SaveMarketDraft()
    {
        if(_marketDraftDirty&&_readMarketDraft!=null)_marketDrafts[_marketResource]=_readMarketDraft();
        _readMarketDraft=null;_marketDraftDirty=false;
    }
    private void ResetMarket()
    {
        SaveMarketDraft();_marketEpoch++;_marketData=null;_marketQuote=null;_marketAvailability.Clear();
        ClearHumanChildren(_marketFooter);_marketFooter.Hide();
    }
    private void MarketReceived(string operation,bool success)
    {
        if(success&&operation is "auto_trade" or "remove_auto_trade")
        {_marketDrafts.Remove(_marketResource);_readMarketDraft=null;_marketDraftDirty=false;}
    }
    private void RefreshMarketButtons()
    {
        foreach(var (button,available) in _marketAvailability)
            if(IsInstanceValid(button))button.Disabled=!(button.Name.ToString().StartsWith("Market_buy_")||button.Name.ToString().StartsWith("Market_sell_")?HumanCanQueue:HumanCanWrite)||!available;
    }
    private void RefreshLiveMarket()
    {
        if(_humanPage!="market"||_marketData==null)return;
        int epoch=_marketEpoch;string resource=_marketResource;
        HumanQuery("market_options",new JsonObject { ["resource"]=resource },result=>
        {
            if(epoch!=_marketEpoch||resource!=_marketResource||_humanPage!="market")return;
            var data=result["data"]!.AsObject();_marketData=data;
            var stocks=data["stocks"]!;var market=data["market"]!;
            void Set(string name,string value){if(_humanContent.FindChild(name,true,false) is Label label)label.Text=value;}
            Set("MarketCash","金币 "+Quantity(stocks[S(market["currency"])]));
            foreach(var row in market["prices"]!.AsArray())
            {
                string key=S(row![0]);Set("MarketAmount_"+key.Split(':')[^1],Quantity(stocks[key]));
                if(_humanContent.FindChild("MarketResource_"+key.Split(':')[^1],true,false) is Button resourceButton)
                    resourceButton.TooltipText=HumanLabel(key)+"\n本国库存 "+Quantity(stocks[key])+"\n市场库存 "+
                        Quantity(market["inventory"]!.AsArray().First(p=>S(p![0])==key)![1])+" / 期望库存 "+
                        Quantity(market["reference"]!.AsArray().First(p=>S(p![0])==key)![1]);
            }
            Set("MarketOwnStock",Quantity(stocks[resource]));
            Set("MarketInventory",Quantity(market["inventory"]!.AsArray().First(p=>S(p![0])==resource)![1]));
            Set("MarketReference",Quantity(market["reference"]!.AsArray().First(p=>S(p![0])==resource)![1]));
            foreach(var quote in data["quotes"]!.AsArray())
                if(_humanContent.FindChild($"Market_{S(quote!["side"])}_{I(quote["quantity"])}",true,false) is Button button)
                {
                    bool available=Yes(quote["available"]);_marketAvailability[button]=available;
                    button.Text=(S(quote["side"])=="buy"?"+":"−")+I(quote["quantity"])+"\n"+(quote["quote"]==null?"缺货":Quantity(quote["quote"]!["gold"])+"金");
                    button.TooltipText=MarketQuoteText(quote);button.Modulate=new Color(available?"ffffff":"969d92");
                    if(_marketQuote!=null&&button.GetGlobalRect().HasPoint(GetGlobalMousePosition()))_marketQuote.Text=button.TooltipText;
                }
            RefreshMarketButtons();
        });
    }
    private void RenderHumanMarket()
    {
        HumanText("市集",true);HumanText("正在读取整单价格…",color:"a7b8a9");
        var ids=_map.Frame!["market"]!.AsArray().Select(m=>S(m!["id"])).ToArray();
        if(ids.Length==0){HumanText("当前没有上市商品。");return;}
        if(!ids.Contains(_marketResource))_marketResource=ids[0];
        string resource=_marketResource;int epoch=_marketEpoch;
        HumanQuery("market_options",new JsonObject { ["resource"]=resource },row=>
        {
            if(epoch!=_marketEpoch||_humanPage!="market"||resource!=_marketResource)return;
            _marketData=row["data"]!.AsObject();FillHumanMarket();
        });
    }
    private void FillHumanMarket()
    {
        if(_marketData==null)return;
        SaveMarketDraft();ClearHumanChildren(_humanContent);ClearHumanChildren(_marketFooter);_marketFooter.Hide();_marketAvailability.Clear();_humanWrites.Clear();_humanSubmit=null;
        var data=_marketData;var market=data["market"]!;var stocks=data["stocks"]!;
        var inventory=market["inventory"]!.AsArray().ToDictionary(p=>S(p![0]),p=>p![1]);
        var reference=market["reference"]!.AsArray().ToDictionary(p=>S(p![0]),p=>p![1]);
        var automatic=market["automatic"]!;
        var heading=new HBoxContainer();_humanContent.AddChild(heading);
        heading.AddChild(new TextureRect { Texture=HumanArt.Texture("icon-market"),CustomMinimumSize=new Vector2(32,32),ExpandMode=TextureRect.ExpandModeEnum.IgnoreSize,StretchMode=TextureRect.StretchModeEnum.KeepAspectCentered });
        var title=new Label { Text="国家市集",SizeFlagsHorizontal=SizeFlags.ExpandFill };title.AddThemeFontSizeOverride("font_size",22);heading.AddChild(title);
        heading.AddChild(new Label { Name="MarketCash",Text="金币 "+Quantity(stocks[S(market["currency"]) ]),Modulate=new Color("ead69b") });
        var grid=new GridContainer { Name="MarketResources",Columns=6,SizeFlagsHorizontal=SizeFlags.ExpandFill };
        grid.AddThemeConstantOverride("h_separation",6);grid.AddThemeConstantOverride("v_separation",6);_humanContent.AddChild(grid);
        foreach(var row in market["prices"]!.AsArray())
        {
            string key=S(row![0]);bool selected=key==_marketResource;
            var rule=automatic["rules"]!.AsArray().FirstOrDefault(r=>S(r!["resource"])==key);
            var button=new Button { Name="MarketResource_"+key.Split(':')[^1],ToggleMode=true,ButtonPressed=selected,
                CustomMinimumSize=new Vector2(78,88),SizeFlagsHorizontal=SizeFlags.ExpandFill,
                TooltipText=HumanLabel(key)+$"\n本国库存 {Quantity(stocks[key])}\n市场库存 {Quantity(inventory[key])} / 期望库存 {Quantity(reference[key])}"+(rule==null?"":Yes(rule["enabled"])?"\n已启用自动交易":"\n自动交易已暂停") };
            HumanArt.Style(button);grid.AddChild(button);
            var body=new VBoxContainer { MouseFilter=MouseFilterEnum.Ignore };body.AddThemeConstantOverride("separation",1);body.SetAnchorsAndOffsetsPreset(LayoutPreset.FullRect);body.OffsetLeft=5;body.OffsetTop=5;body.OffsetRight=-5;body.OffsetBottom=-5;button.AddChild(body);
            body.AddChild(new TextureRect { Texture=RtsArt.Resource(key),CustomMinimumSize=new Vector2(26,26),ExpandMode=TextureRect.ExpandModeEnum.IgnoreSize,StretchMode=TextureRect.StretchModeEnum.KeepAspectCentered,MouseFilter=MouseFilterEnum.Ignore });
            var text=new VBoxContainer { MouseFilter=MouseFilterEnum.Ignore,SizeFlagsHorizontal=SizeFlags.ExpandFill };body.AddChild(text);
            var label=new Label { Text=HumanLabel(key)+(rule!=null&&Yes(rule["enabled"])?"·":""),HorizontalAlignment=HorizontalAlignment.Center,MouseFilter=MouseFilterEnum.Ignore,Modulate=new Color(selected?"efda93":"cddbc5") };label.AddThemeFontSizeOverride("font_size",16);text.AddChild(label);
            var amount=new Label { Name="MarketAmount_"+key.Split(':')[^1],Text=Quantity(stocks[key]),HorizontalAlignment=HorizontalAlignment.Center,MouseFilter=MouseFilterEnum.Ignore,TextOverrunBehavior=TextServer.OverrunBehavior.TrimEllipsis };amount.AddThemeFontSizeOverride("font_size",15);text.AddChild(amount);
            button.Pressed+=()=>{SaveMarketDraft();_marketResource=key;RenderHumanPage();};
        }
        var tabs=new HBoxContainer();_humanContent.AddChild(tabs);
        foreach(var (auto,text) in new[]{(false,"即时交易"),(true,"自动交易")})
        {
            bool mode=auto;var tab=Button(tabs,text,()=>{SaveMarketDraft();_marketAutomatic=mode;FillHumanMarket();});
            tab.Name=mode?"MarketAutoTab":"MarketManualTab";
            HumanArt.Style(tab,"market");tab.SizeFlagsHorizontal=SizeFlags.ExpandFill;tab.Disabled=mode==_marketAutomatic;
        }
        HumanText(HumanLabel(_marketResource),true);
        var balances=new HBoxContainer { Name="MarketStocks" };_humanContent.AddChild(balances);
        foreach(var (name,text,value) in new[]{("MarketOwnStock","本国库存",stocks[_marketResource]),("MarketInventory","市场库存",inventory[_marketResource]),("MarketReference","市场期望库存",reference[_marketResource])})
        {
            var box=new VBoxContainer { SizeFlagsHorizontal=SizeFlags.ExpandFill };balances.AddChild(box);
            var label=new Label { Text=text,Modulate=new Color("afc0a7") };label.AddThemeFontSizeOverride("font_size",14);box.AddChild(label);
            var amount=new Label { Name=name,Text=Quantity(value) };amount.AddThemeFontSizeOverride("font_size",18);box.AddChild(amount);
        }
        balances.TooltipText="市场期望库存是定价参考；本国自动交易目标在下方单独设置。";
        var ownRule=automatic["rules"]!.AsArray().FirstOrDefault(r=>S(r!["resource"])==_marketResource);
        if(ownRule!=null)HumanText("本国自动目标："+(ownRule["buy_below"]==null?"不自动买入":"低于 "+Quantity(ownRule["buy_below"])+" 补货")+" · "+
            (ownRule["sell_above"]==null?"不自动卖出":"高于 "+Quantity(ownRule["sell_above"])+" 出售")+(Yes(ownRule["enabled"])?"":"（已暂停）"),color:"b9cda9").Name="MarketOwnTargets";
        if(_marketAutomatic)FillAutoTrade(automatic);
        else FillManualMarket(data["quotes"]!.AsArray());
        RefreshHumanButtons();
    }
    private void FillManualMarket(JsonArray quotes)
    {
        foreach(var (side,label,color,sign) in new[]{("buy","买入","a8d5b2","+"),("sell","卖出","e7b89a","−")})
        {
            HumanText(label,color:color);
            var row=new HBoxContainer { SizeFlagsHorizontal=SizeFlags.ExpandFill };row.AddThemeConstantOverride("separation",5);_humanContent.AddChild(row);
            foreach(var entry in quotes.Where(q=>S(q!["side"])==side))
            {
                var item=entry!;int quantity=I(item["quantity"]);string command=side;bool available=Yes(item["available"]);
                string cost=item["quote"]==null?"缺货":Quantity(item["quote"]!["gold"])+"金";
                var button=new Button { Name=$"Market_{side}_{quantity}",Text=sign+quantity+"\n"+cost,SizeFlagsHorizontal=SizeFlags.ExpandFill,
                    CustomMinimumSize=new Vector2(68,62),MouseDefaultCursorShape=CursorShape.PointingHand };
                HumanArt.Style(button);button.AddThemeFontSizeOverride("font_size",16);row.AddChild(button);_marketAvailability[button]=available;
                button.Modulate=new Color(available?"ffffff":"969d92");
                string message=MarketQuoteText(item);button.TooltipText=message;
                string CurrentQuote()=>MarketQuoteText(_marketData?["quotes"]?.AsArray().FirstOrDefault(q=>S(q!["side"])==command&&I(q["quantity"])==quantity)??item);
                button.MouseEntered+=()=>{if(_marketQuote!=null)_marketQuote.Text=CurrentQuote();};
                button.FocusEntered+=()=>{if(_marketQuote!=null)_marketQuote.Text=CurrentQuote();};
                button.Pressed+=()=>{if(_marketAvailability.GetValueOrDefault(button))SubmitHuman(command,new JsonObject { ["resource"]=_marketResource,["quantity"]=quantity });};
                if(side=="buy"&&quantity==1)_humanSubmit=button;
            }
        }
        _marketQuote=HumanText("按钮下方为当前整单报价，已计入价差、滑点与取整。\n演出中可连续点击排队；每笔按执行时的价格和库存核算。",color:"afc2ac");
        _marketQuote.AddThemeFontSizeOverride("font_size",14);
    }
    private string MarketQuoteText(JsonNode row)
    {
        string name=HumanLabel(_marketResource),side=S(row["side"])=="buy"?"买入":"卖出";
        string result=$"{side} {name} ×{row["quantity"]}";
        if(row["quote"] is JsonNode quote)
            result+=$" · {(side=="买入"?"支付":"收入")} {Quantity(quote["gold"])} 金\n整单均价 {F(quote["exact_total"])/I(row["quantity"]):0.####} 金/单位";
        if(!Yes(row["available"]))result+="\n"+LocalHumanError(S(row["reason"]));
        return result;
    }
    private void FillAutoTrade(JsonNode automatic)
    {
        var saved=automatic["rules"]!.AsArray().FirstOrDefault(r=>S(r!["resource"])==_marketResource);
        var draft=_marketDrafts.GetValueOrDefault(_marketResource)??saved?.AsObject();
        HumanText("本国结束行动时执行一次，先卖盈余、再买短缺。",color:"adc4ab").AddThemeFontSizeOverride("font_size",14);
        var active=new CheckButton { Name="AutoTradeEnabled",Text="启用自动交易",ButtonPressed=draft==null||Yes(draft["enabled"]) };_humanContent.AddChild(active);
        var buy=new CheckButton { Name="AutoTradeBuy",Text="自动购买 · 补足库存",ButtonPressed=draft?["buy_below"]!=null };_humanContent.AddChild(buy);
        var buyTarget=MarketNumber("低于此库存时补货",draft?["buy_below"]==null?50:F(draft["buy_below"]),"AutoBuyTarget");
        var buyLimit=MarketNumber("每回合最多购买",draft?["buy_limit"]==null?50:F(draft["buy_limit"]),"AutoBuyLimit",1);
        var sell=new CheckButton { Name="AutoTradeSell",Text="自动卖出 · 售出盈余",ButtonPressed=draft?["sell_above"]!=null };_humanContent.AddChild(sell);
        var sellTarget=MarketNumber("高于此库存时出售",draft?["sell_above"]==null?200:F(draft["sell_above"]),"AutoSellTarget");
        var sellLimit=MarketNumber("每回合最多出售",draft?["sell_limit"]==null?50:F(draft["sell_limit"]),"AutoSellLimit",1);
        var advanced=new CheckButton { Name="AutoTradeAdvanced",Text="价格与现金保护",ButtonPressed=_marketAdvanced };_humanContent.AddChild(advanced);
        var settings=new VBoxContainer { Visible=_marketAdvanced };_humanContent.AddChild(settings);
        var reserve=MarketNumber("至少保留金币",F(draft?["reserve_gold"]),"AutoReserve",parent:settings);
        var ceiling=new CheckButton { Text="限制最高购买均价",ButtonPressed=draft?["max_buy_price"]!=null };settings.AddChild(ceiling);
        var maxPrice=MarketNumber("买入均价 ≤",draft?["max_buy_price"]==null?10:F(draft["max_buy_price"]),"AutoMaxPrice",parent:settings,step:.01);
        var floor=new CheckButton { Text="限制最低出售均价",ButtonPressed=draft?["min_sell_price"]!=null };settings.AddChild(floor);
        var minPrice=MarketNumber("卖出均价 ≥",draft?["min_sell_price"]==null?1:F(draft["min_sell_price"]),"AutoMinPrice",parent:settings,step:.01);
        _readMarketDraft=()=>new JsonObject { ["resource"]=_marketResource,["enabled"]=active.ButtonPressed,
            ["buy_below"]=buy.ButtonPressed?(JsonNode?)JsonValue.Create((int)buyTarget.Value):null,
            ["sell_above"]=sell.ButtonPressed?(JsonNode?)JsonValue.Create((int)sellTarget.Value):null,
            ["buy_limit"]=(int)buyLimit.Value,["sell_limit"]=(int)sellLimit.Value,["reserve_gold"]=(int)reserve.Value,
            ["max_buy_price"]=ceiling.ButtonPressed?(JsonNode?)JsonValue.Create(maxPrice.Value):null,
            ["min_sell_price"]=floor.ButtonPressed?(JsonNode?)JsonValue.Create(minPrice.Value):null };
        var warning=HumanText("",color:"e6b59b");
        _marketFooter.Show();
        var save=Button(_marketFooter,"保存自动交易",()=>
        {
            if(_readMarketDraft==null)return;
            SubmitHuman("auto_trade",_readMarketDraft());
        });HumanArt.Style(save,"check");save.Name="AutoTradeSave";save.SizeFlagsHorizontal=SizeFlags.ExpandFill;
        int validation=0;int epoch=_marketEpoch;
        async void Validate()
        {
            int request=++validation;
            _marketAvailability[save]=false;warning.Text="正在核对交易条件…";
            buyTarget.Editable=buy.ButtonPressed;buyLimit.Editable=buy.ButtonPressed;
            sellTarget.Editable=sell.ButtonPressed;sellLimit.Editable=sell.ButtonPressed;
            maxPrice.Editable=ceiling.ButtonPressed;minPrice.Editable=floor.ButtonPressed;RefreshMarketButtons();
            await ToSignal(GetTree().CreateTimer(.15),SceneTreeTimer.SignalName.Timeout);
            if(request!=validation||epoch!=_marketEpoch||_readMarketDraft==null)return;
            HumanQuery("auto_trade_options",_readMarketDraft(),result=>
            {
                if(request!=validation||epoch!=_marketEpoch||!IsInstanceValid(save))return;
                bool valid=Yes(result["data"]?["available"]);
                warning.Text=valid?"":S(result["data"]?["reason"]);
                save.TooltipText=valid?"保存当前商品的设置；本国结束行动时才执行":warning.Text;
                _marketAvailability[save]=valid;RefreshMarketButtons();
            });
        }
        foreach(var check in new[]{active,buy,sell,ceiling,floor})check.Toggled+=_=>{_marketDraftDirty=true;Validate();};
        foreach(var field in new[]{buyTarget,buyLimit,sellTarget,sellLimit,reserve,maxPrice,minPrice})field.ValueChanged+=_=>{_marketDraftDirty=true;Validate();};
        advanced.Toggled+=on=>{_marketAdvanced=on;settings.Visible=on;};
        if(saved!=null)
        {
            var remove=Button(_marketFooter,"删除规则",()=>SubmitHuman("remove_auto_trade",new JsonObject { ["resource"]=_marketResource }));HumanArt.Style(remove,"disband");
            remove.Name="AutoTradeRemove";_marketAvailability[remove]=true;
        }
        if(_marketDrafts.ContainsKey(_marketResource))HumanText("有尚未保存的修改。",color:"dfc78e");
        var last=automatic["last"];
        if(last!=null)
        {
            HumanText($"最近执行 · 第 {I(last["period"])+1} 回合",true);
            var orders=last["orders"]!.AsArray().Where(o=>S(o!["resource"])==_marketResource).ToArray();
            if(orders.Length==0)HumanText("库存未触发交易，或此商品未启用。",color:"a4b69f");
            foreach(var order in orders)HumanText($"{(S(order!["side"])=="buy"?"买入":"卖出")} {order["quantity"]}/{order["requested"]}"+
                (I(order["quantity"])>0?$" · {Quantity(order["gold"])} 金"+ (S(order["status"])=="partial"?" · 部分成交":"") :" · "+S(order["reason"])),color:I(order["quantity"])>0?"b5d5ad":"dfb494");
        }
        HumanText("余额或市场库存不足时尽量成交；价格按整单均价判断。自动交易不借贷，不绕过市场规则。",color:"a4b69f").AddThemeFontSizeOverride("font_size",14);
        Validate();
    }
    private SpinBox MarketNumber(string label,double value,string name,int min=0,BoxContainer? parent=null,double step=1)
    {
        var row=new HBoxContainer();(parent??_humanContent).AddChild(row);
        var title=new Label { Text=label,SizeFlagsHorizontal=SizeFlags.ExpandFill };title.AddThemeFontSizeOverride("font_size",16);row.AddChild(title);
        var field=new SpinBox { UpdateOnTextChanged=true,SelectAllOnFocus=true,Name=name,MinValue=min,MaxValue=1000000000,Step=step,Value=value,CustomMinimumSize=new Vector2(148,0) };row.AddChild(field);return field;
    }
}
