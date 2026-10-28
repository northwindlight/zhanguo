using Godot;
using System;
using System.IO;
using System.Linq;
using System.Collections.Generic;
using System.Text.Json;
using System.Text.Json.Nodes;
using System.Threading.Tasks;
using static WorldMap;
using Zhanguo.Desktop.Layout;

public partial class Desktop
{
    private bool _humanReviewDiagnostics;
    private const double HumanRoundTimeout=300;
    private async Task ReviewExtinctionMemory(string path)
    {
        try
        {
            _status.Text="旧地图归属失效 · 离线验收";
            var input=JsonNode.Parse(File.ReadAllText(OS.GetEnvironment("ZHANGUO_EXTINCTION_REVIEW")))!.AsObject();
            var point=new Vector2I(I(input["position"]![0]),I(input["position"]![1]));
            string oldOwner=S(input["old_owner"]),stem=Path.ChangeExtension(path,null);
            _humanStarted=true;_humanPage="tile";_humanCell=point;
            var checks=new JsonArray();
            foreach(var stage in input["frames"]!.AsArray())
            {
                string name=S(stage!["name"]);
                if(name=="restored"){GetWindow().ContentScaleSize=new Vector2I(1024,768);GetWindow().Size=new Vector2I(1024,768);}
                ApplyFrame(stage["frame"]!.DeepClone().AsObject());
                RenderHumanPage();
                OpenDrawer(_humanDrawer);_map.SelectAndFocus(point);
                _bannerTitle.Text=name=="before"?"灭国前 · A持有B的旧地图":"灭国后 · 旧地图归属失效";
                _bannerText.Text="秦攻占楚最后的市政厅；此处在秦的实时视野外。";
                await ToSignal(GetTree().CreateTimer(.6),SceneTreeTimer.SignalName.Timeout);
                _map.SelectAndFocus(point);
                var tile=_map.SelectedTile!;
                var region=WorldLayoutProjection.FromFrame(_map.Frame!).Regions.Single(r=>r.X==point.X&&r.Y==point.Y);
                var caption=(Label)_humanContent.FindChild("HumanOwnership",true,false)!;
                if(!new Rect2(Vector2.Zero,Size).Encloses(caption.GetGlobalRect()))throw new Exception("历史归属说明超出窗口");
                if(S(tile["visibility"])!="memory")throw new Exception("样例失去历史地图权限条件");
                if(name=="before")
                {
                    if(S(tile["owner"])!=oldOwner||region.OwnerColor=="")throw new Exception("旧地图未显示B旧归属");
                }
                else if(S(tile["owner"])!=""||region.OwnerColor!=""||!caption.Text.Contains("现归属待确认"))
                    throw new Exception("灭国旧地图仍有B国色或冒充已知无主地");
                await Screenshot(stem+"-"+name+".png");
                checks.Add(new JsonObject { ["stage"]=name,["owner"]=tile["owner"]?.DeepClone(),["color"]=region.OwnerColor,["caption"]=caption.Text,["width"]=(int)Size.X,["height"]=(int)Size.Y });
            }
            File.WriteAllText(path+".json",checks.ToJsonString(new JsonSerializerOptions { WriteIndented=true }));
            GD.Print("EXTINCTION_MEMORY_GUI_OK "+path);GetTree().Quit();
        }
        catch(Exception e){GD.PrintErr(e);GetTree().Quit(3);}
    }

    private void SelectHumanField(string field,string value)
    {
        var options=HumanFieldOptions(field)!;int index=options.FindIndex(o=>S(o.Value)==value);
        if(index<0)throw new Exception("验收选项不存在："+field+" "+value);
        var picker=_humanContent.FindChild("HumanField_"+field,true,false) as OptionButton;
        picker!.Select(index);picker.EmitSignal(OptionButton.SignalName.ItemSelected,(long)index);
    }
    private async Task RtsReady()=>await WaitFor(()=>_rtsData!=null&&_rtsCards.Count>0,"地块图标候选未返回");
    private async Task MarketReady()=>await WaitFor(()=>_marketData!=null&&_humanDrawer.Body.FindChild(_marketAutomatic?"AutoTradeSave":"Market_buy_1",true,false)!=null,"市场报价没有返回");
    private async Task HoverRts(Button button)
    {
        await WaitFor(()=>Drawers.All(d=>d.IsSettled),"悬停前抽屉未展开");
        for(Node? p=button.GetParent();p!=null;p=p.GetParent())if(p is ScrollContainer scroll)scroll.EnsureControlVisible(button);
        await ToSignal(GetTree(),SceneTree.SignalName.ProcessFrame);await ToSignal(GetTree(),SceneTree.SignalName.ProcessFrame);
        var at=button.GetGlobalRect().GetCenter();GetViewport().PushInput(new InputEventMouseMotion { Position=at,GlobalPosition=at },true);
        await ToSignal(GetTree(),SceneTree.SignalName.ProcessFrame);
    }
    private async Task HumanIdle()=>await WaitFor(()=>HumanCanWrite&&!HasTimeline&&_humanCommands.Count==0,"真人操作或本批演出没有结束",120);
    private async Task CheckGarrisonFirst()
    {
        await ToSignal(GetTree(),SceneTree.SignalName.ProcessFrame);await ToSignal(GetTree(),SceneTree.SignalName.ProcessFrame);
        var title=(Control)_humanContent.FindChild("GarrisonHeading",true,false);
        var summary=(Control)_humanContent.FindChild("GarrisonOverview",true,false);
        var building=_humanContent.FindChild("TileBuildingHeading",true,false);
        var view=_humanScroll.GetGlobalRect();
        if(title==null||summary==null||!view.Encloses(title.GetGlobalRect())||!view.Encloses(summary.GetGlobalRect())||_humanScroll.ScrollVertical!=0)
            throw new Exception("驻军没有在不滚动时显示");
        if(building!=null&&title.GetIndex()>building.GetIndex())throw new Exception("驻军仍然排在建筑下方");
        foreach(string name in new[]{"HumanOrder_move","HumanOrder_attack","HumanArmy1"})
            if(_humanContent.FindChild(name,true,false) is not Control c||!view.Encloses(c.GetGlobalRect()))throw new Exception("驻军选择或军令按钮需要滚动："+name);
    }
    private void ValidateHumanLayout()
    {
        var viewport=new Rect2(Vector2.Zero,Size);
        if(!viewport.Encloses(_humanDock.GetGlobalRect()))throw new Exception("真人底部栏超出窗口");
        foreach(var b in _humanNav.Values)if(!viewport.Encloses(b.GetGlobalRect()))throw new Exception("真人按钮超出窗口："+b.Text);
        if(_humanDrawer.IsOpen&&!viewport.Encloses(_humanDrawer.GetGlobalRect()))throw new Exception("真人抽屉超出窗口");
        if(_humanDrawer.IsOpen&&_humanPage=="market"&&_marketAutomatic)
        {
            if(!_marketFooter.IsVisibleInTree()||!viewport.Encloses(_marketFooter.GetGlobalRect()))throw new Exception("自动交易固定底栏不可见");
            foreach(var button in _marketFooter.GetChildren().OfType<Button>())
                if(!viewport.Encloses(button.GetGlobalRect()))throw new Exception("自动交易保存／删除按钮超出窗口");
        }
        void Check(Node node)
        {
            if(node is ScrollContainer scroll&&scroll.IsVisibleInTree()&&scroll.GetHScrollBar().MaxValue>scroll.GetHScrollBar().Page+1)
                throw new Exception("真人面板横向溢出");
            foreach(Node c in node.GetChildren())Check(c);
        }
        Check(_humanDrawer);ValidateLedgerLayout();
        if(_ledgerActor!=HumanActor||_map.Frame!["nations"]!.AsArray().Count!=1)throw new Exception("真人账目显示了其他国家");
        if(_map.Frame["tiles"]!.AsArray().Any(t=>S(t!["visibility"])=="unknown"&&t["terrain"]!=null))throw new Exception("未知地块带有秘密地形");
    }
    private async Task ReviewHuman(string path)
    {
        _humanReviewDiagnostics=true;
        try
        {
            ConfirmHuman("窗口输入验收","返回应取消，不执行操作。",()=>throw new Exception("取消却执行确认"));
            await PointerClick(_humanConfirm.GetCancelButton());
            if(_humanConfirm.Visible)throw new Exception("真实指针没有关闭确认窗口");
            bool confirmed=false;ConfirmHuman("窗口输入验收","确认按钮应执行一次。",()=>confirmed=true);
            await PointerClick(_humanConfirm.GetOkButton());
            if(!confirmed||_humanConfirm.Visible)throw new Exception("真实指针没有确认操作");
            GD.Print("HUMAN_DIALOG_OK");
            string stem=Path.ChangeExtension(Path.GetFullPath(path),null);
            string reviewRoot=stem+"-data";Directory.CreateDirectory(reviewRoot);
            _runtime=_runtime with { DataRoot=reviewRoot };
            _menu=new MainMenu(reviewRoot,MenuBegin);_menu.SetAnchorsAndOffsetsPreset(LayoutPreset.FullRect);_menu.ZIndex=30;AddChild(_menu);
            _menu.OpenPlayerGame();_menu.PlayerSeedField.Text="20261026";
            _menu.PlayerNameField.Text="青岚";_menu.PlayerColorField.Color=new Color("66b6a7");await TypeNumber(_menu.PlayerMapSizeField,"32");
            await ToSignal(GetTree().CreateTimer(.3),SceneTreeTimer.SignalName.Timeout);
            _menu.ValidateVisibleLayout();await Screenshot(stem+"-player-step1.png");
            await PointerClick(_menu.PlayerNextButton);
            var freeHuman=_menu.BuildConfig(out string freeError,out _);
            if(freeHuman==null||freeHuman["scenario"]!=null||freeHuman["nations"]!.AsArray().Count(n=>S(n!["client"])=="human")!=1||I(freeHuman["map_size"])!=32)
                throw new Exception("自由玩家两步配置错误："+freeError);
            _menu.ReviewHumanCard(1);
            if(_menu.BuildConfig(out string multiError,out _)!=null||!multiError.Contains("一位真人"))throw new Exception("菜单未阻止多真人席位");
            _menu.SelectScenario(1);_menu.ReviewHumanCard();
            await PointerClick(_menu.PlayerBackButton);
            if(_menu.PlayerNameField.Text!="青岚"||_menu.PlayerColorField.Color.ToHtml(false)!="66b6a7")throw new Exception("返回上一步丢失本国定制");
            _menu.SelectPlayerScenario(0);await PointerClick(_menu.PlayerNextButton);
            _menu.ReviewRuleSeeds(20261026);
            _menu.ValidateVisibleLayout();await Screenshot(stem+"-menu.png");
            var config=_menu.BuildConfig(out string error,out string? configPath);
            if(config==null||configPath==null||config["nations"]!.AsArray().Count(n=>S(n!["client"])=="human")!=1)throw new Exception("真人菜单配置失败："+error);
            if(config["nations"]!.AsArray().Where(n=>S(n!["client"])=="rule").Any(n=>n!["rule_seed"]==null))throw new Exception("离线验收的规则AI缺少固定种子");
            _menu.Visible=false;StartHost(configPath,null);
            await HumanIdle();if(_map.NationName(HumanActor)!="青岚"||_map.NationColor(HumanActor).ToHtml(false)!="66b6a7")throw new Exception("本国名称／颜色未进入实际地图");
            if(OwnArmies.Any())throw new Exception("正常真人开局不应赠兵");CloseDrawers(false);_map.Fit();await Screenshot(path);
            await ReviewMapTools(stem);
            if(!_ledgerPowerBox.Visible||!_ledgerPower.Text.Contains("0/1")||!_ledgerPowerLast.Text.Contains("尚未结算"))throw new Exception("开局未显示当前电力缺口与首次结算状态");
            foreach(string page in new[]{"build","army","market","diplomacy","alliance","intel","nation"})
            {
                await PointerClick(_humanNav[page]);await ToSignal(GetTree(),SceneTree.SignalName.ProcessFrame);
                if(page=="market")await MarketReady();
                ValidateHumanLayout();await Screenshot(stem+"-"+page+".png");
            }
            // Repeated clicks are distinct queued orders; no wait for each animation.
            await PointerClick(_humanNav["market"]);await MarketReady();double food=F(HumanFacts["econ"]!["stocks"]!.AsArray().First(s=>S(s!["id"])=="native:food")!["amount"]);
            int history=_humanHistory.Count;float priorSpeed=_playbackSpeed;int priorIndex=_speedControl.Selected;
            _speedControl.Select(0);_speedControl.EmitSignal(OptionButton.SignalName.ItemSelected,0L);
            var once=_humanSubmit!;await PointerClick(once);
            await WaitFor(()=>HasTimeline&&_humanBusy,"首笔买入没有开始演出");
            if(EffectivePlaybackSpeed!=.5f||_speedControl.Text=="行动 1×")throw new Exception("真人操作仍强制跳回1倍速");
            await WaitFor(()=>(_humanContent.FindChild("MarketOwnStock",true,false) as Label)?.Text==Quantity(System.Text.Json.Nodes.JsonValue.Create(food+1)),"演出期间市场本国库存仍旧，未随成交更新");
            await PointerClick(once);
            if(_humanCommands.Count!=1||!_humanQueuePanel.Visible)throw new Exception("连续买入没有进入待执行队列");
            await Screenshot(stem+"-market-queue.png");await HumanIdle();await MarketReady();
            if(F(HumanFacts["econ"]!["stocks"]!.AsArray().First(s=>S(s!["id"])=="native:food")!["amount"])!=food+2||_humanHistory.Count!=history+2)
                throw new Exception($"连续两次买入尚未对账：粮食 {OwnStock("food")} / {food+2}，回执 {_humanHistory.Count-history}/2，状态 {_hostState}，忙碌 {_humanBusy}，待执行 {_humanCommands.Count}");
            await PointerClick(MarketReviewButton("Market_sell_1"));await WaitFor(()=>HasTimeline&&_humanBusy,"卖出演出没有开始");
            await PointerClick(MarketReviewButton("Market_sell_10"));
            if(_humanCommands.Count!=1)throw new Exception("卖出未排队");
            await PointerClick((Button)_humanDrawer.Body.FindChild("CancelQueuedHuman",true,false)!);
            await HumanIdle();await MarketReady();
            if(OwnStock("food")!=food+1||_humanHistory.Count!=history+3||_humanCommands.Count!=0)throw new Exception("取消待执行指令后仍成交，或撤销了已执行交易");
            _playbackSpeed=priorSpeed;_speedControl.Select(priorIndex);RefreshSpeedDisplay();
            // Real diplomacy form: recipient list and body, no tool IDs or JSON.
            _humanOperation="send_letter";await PointerClick(_humanNav["diplomacy"]);
            var letterBody=_humanContent.FindChild("HumanField_text",true,false) as TextEdit;
            letterBody!.Text="愿两国互通有无，商议和平。";letterBody.EmitSignal(TextEdit.SignalName.TextChanged);
            RequestHumanPreview();await WaitFor(()=>_humanPreview!.Text.Contains("字"),"信件预览没有返回");
            await PointerClick(_humanSubmit!);await HumanIdle();
            if(!HumanFacts["dispatches"]!.AsArray().Any(d=>S(d!["kind"])=="letter"))throw new Exception("写信表单未发出真实信件："+_humanReceipt.Text);
            var home=_map.Frame!["tiles"]!.AsArray().First(t=>S(t!["owner"])==HumanActor&&t["buildings"]!.AsArray().Any(b=>S(b![0])=="native:town-hall"))!;
            var capital=new Vector2I(I(home["position"]![0]),I(home["position"]![1]));
            CloseDrawers(false);_map.SelectAndFocus(capital);await PointerClickAt(_map.CellCenter(capital));
            if(_humanPage!="tile")throw new Exception("地块点击未打开真人驻军面板");
            _productionQueueMode=false;OpenHumanPage("build");await RtsReady();
            if(!_rtsCards["native:barracks"].Disabled)throw new Exception("缺前置建筑的兵营未变暗");
            await HoverRts(_rtsCards["native:barracks"]);
            await WaitFor(()=>_rtsTooltip?.Visible==true,"变暗候选无法悬停解释条件");await Screenshot(stem+"-locked.png");
            await HoverRts(_rtsCards["native:militia-camp"]);
            await WaitFor(()=>_rtsTooltip?.Visible==true,"短悬停没有出现真实价格与条件");
            await Screenshot(stem+"-price.png");
            await WaitFor(()=>_rtsExpanded,"长悬停没有作用与收益");await Screenshot(stem+"-quote.png");
            await PointerClick(_rtsCards["native:militia-camp"]);await HumanIdle();await RtsReady();
            if(_rtsCards.Values.Any(b=>_rtsAvailability[b]))throw new Exception("施工额度用尽后仍有可建候选");
            if(!HumanFacts["building_sites"]!["instances"]!.AsArray().Any(b=>S(b!["kind"])=="native:militia-camp"&&I(b["remaining"])==1))throw new Exception("建设没有真实动工");
            var other=HumanFacts["building_sites"]!["sites"]!.AsArray().First(t=>I(t!["position"]![0])!=capital.X||I(t["position"]![1])!=capital.Y)!["position"]!;
            var otherCell=new Vector2I(I(other[0]),I(other[1]));RtsChangeCell(otherCell);await RtsReady();
            if(_rtsCards["native:castle"].Disabled||Yes(_rtsData!["ordered"])||!_humanReceipt.Text.Contains("已切换地块"))throw new Exception("A 地块的状态残留到 B 地块");
            await PointerClick(_rtsCards["native:castle"]);await HumanIdle();await RtsReady();
            RtsChangeCell(capital);await RtsReady();
            if(!Yes(_rtsData!["ordered"])||_rtsCards.Values.Any(b=>_rtsAvailability[b]))throw new Exception("返回 A 后丢失本期施工状态");
            await Screenshot(stem+"-quota.png");
            await ReviewMarketDemolition(stem,capital,otherCell);
            await PointerClick(_humanNav["end"]);
            await WaitFor(()=>S(_playingBeat?["phase"])=="production","本国生产没有演出",HumanRoundTimeout);
            if(!_settlementCard.Visible||_humanDrawer.IsOpen)throw new Exception("生产汇总被操作面板遮住");
            await Screenshot(stem+"-production.png");
            await WaitFor(()=>HumanCanWrite&&_period==1,"真人回合没有轮换与结算",HumanRoundTimeout);
            var autoOrders=HumanFacts["market"]!["automatic"]!["last"]!["orders"]!.AsArray();
            if(!autoOrders.Any(o=>S(o!["side"])=="buy"&&I(o["quantity"])>0)||!autoOrders.Any(o=>S(o!["side"])=="sell"&&I(o["quantity"])>0))throw new Exception("自动买卖没有在结束行动时真实成交");
            if(NationLedger.Read(_map.Frame!,HumanActor)?.Power is not {Capacity:2,Generated:2,Demand:1,PreviousShort:true}||!_ledgerPowerLast.Text.Contains("不足"))throw new Exception("电厂落成后的供电预估与上期实际电网没有区分");
            await Screenshot(stem+"-power.png");
            await ReviewCompletedDemolition(stem,otherCell);
            _humanCell=capital;OpenHumanPage("recruit");await RtsReady();
            if(_rtsCards["native:militia"].Disabled||!_rtsCards["native:infantry"].Disabled)throw new Exception("军屯与兵营候选未正确筛选");
            await TypeNumber((SpinBox)_humanContent.FindChild("RtsRecruitCount",true,false)!,"2");
            await WaitFor(()=>!_rtsCountPending&&_rtsData!=null,"征召数量没有刷新报价");await RtsReady();
            if(!_rtsCards["native:militia"].Disabled)throw new Exception("输入2支却仍采用旧的1支征召条件");
            await TypeNumber((SpinBox)_humanContent.FindChild("RtsRecruitCount",true,false)!,"1");
            await WaitFor(()=>!_rtsCountPending&&_rtsData!=null,"征召数量改回后没有刷新");await RtsReady();
            await Screenshot(stem+"-recruit.png");await PointerClick(_rtsCards["native:militia"]);await HumanIdle();
            if(!OwnArmies.Any())throw new Exception("真人征兵没有产生真实军队："+_humanReceipt.Text);
            await PointerClick(_humanNav["end"]);await WaitFor(()=>HumanCanWrite&&_period==2,"新兵没有进入下一期",HumanRoundTimeout);
            _humanCell=capital;OpenHumanPage("tile");
            await CheckGarrisonFirst();await Screenshot(stem+"-garrison.png");
            var check=_humanContent.FindChild("HumanArmy1",true,false) as CheckButton;
            if(check==null)throw new Exception("本国驻军没有复选框");await PointerClick(check);
            StartHumanTarget("move");await WaitFor(()=>_map.TargetCount>0,"没有逐格移动目标圈");
            await Screenshot(stem+"-move.png");
            GetViewport().PushInput(new InputEventKey { Keycode=Key.Escape,Pressed=true },true);
            if(_map.TargetCount!=0||_pauseMenu.Visible)throw new Exception("Esc 未优先取消军令");
            StartHumanTarget("move");await WaitFor(()=>_map.TargetCount>0,"取消后没有恢复军令范围");
            GetViewport().PushInput(new InputEventKey { Keycode=Key.Ctrl,Pressed=true },true);
            await WaitFor(()=>_humanTargetMode=="attack"&&_map.TargetCount>0,"Ctrl没有切换攻击候选");
            GetViewport().PushInput(new InputEventKey { Keycode=Key.Ctrl,Pressed=false },true);
            await WaitFor(()=>_humanTargetMode=="move"&&_map.TargetCount>0,"松开Ctrl没有恢复移动候选");
            var target=_map.Frame!["tiles"]!.AsArray().First(t=>S(t!["visibility"])=="live"&&t["owner"]==null&&
                _map.HumanTarget(new(I(t["position"]![0]),I(t["position"]![1])))!=null)!;
            var destination=new Vector2I(I(target["position"]![0]),I(target["position"]![1]));
            await RightOrderAt(destination);await HumanIdle();
            if(!OwnArmies.Any(u=>I(u["x"])==destination.X&&I(u["y"])==destination.Y)||S(_map.Frame["tiles"]!.AsArray().First(t=>I(t!["position"]![0])==destination.X&&I(t["position"]![1])==destination.Y)!["owner"])!="")
                throw new Exception("移动没有执行或被错误转成占领");
            var wild=SquadView.Units(_map.Frame!).Single(u=>S(u["owner"])=="野人"&&I(u["x"])==destination.X&&I(u["y"])==destination.Y);
            string wildKey=DesktopScene.Key("armies",wild);int wildId=I(wild["id"]);
            _map.SelectAndFocus(destination);await ToSignal(GetTree(),SceneTree.SignalName.ProcessFrame);await ToSignal(GetTree(),SceneTree.SignalName.ProcessFrame);
            int wildFigures=_map.WildFigureCounts.GetValueOrDefault(wildKey);
            if(wildFigures<1)throw new Exception("待战野人没有统一编队");await Screenshot(stem+"-wild-idle.png");
            _humanCell=destination;OpenHumanPage("tile");StartHumanTarget("attack");await WaitFor(()=>_map.TargetCount>0,"原地攻击没有独立准星圈");
            await Screenshot(stem+"-attack.png");await RightOrderAt(destination,attack:true);await HumanIdle();
            if(!OwnArmies.Any(u=>u["engaged"]?.GetValue<bool>()==true))throw new Exception("进攻没有按真实规则登记交战");
            _humanOperation="buy_map";await PointerClick(_humanNav["intel"]);await PointerClick(_humanSubmit!);await HumanIdle();
            if(_map.MemoryCount==0)throw new Exception("购图没有合入历史地图");
            var memory=_map.Frame!["tiles"]!.AsArray().First(t=>S(t!["visibility"])=="memory")!;
            var memoryCell=new Vector2I(I(memory["position"]![0]),I(memory["position"]![1]));
            CloseDrawers(false);_map.SelectAndFocus(memoryCell);await Screenshot(stem+"-memory.png");
            var remembered=_map.Frame!.ToJsonString();int known=_map.SimpleVisibleTiles,unknown=_map.UnknownCount;
            await PointerClick(_terrainModeButton);_map.SelectAndFocus(memoryCell);await Screenshot(stem+"-simple-memory.png");
            if(!_map.SimpleTerrain||known!=_map.SimpleVisibleTiles||unknown!=_map.UnknownCount||remembered!=_map.Frame.ToJsonString())throw new Exception("简易地图改变了历史或未知信息");
            await PointerClick(_terrainModeButton);
            var center=_map.CellCenter(memoryCell);int radius=(int)Math.Max(8,48*_map.Zoom);
            var rect=new Rect2I((int)center.X-radius,(int)center.Y-radius,radius*2,radius*2);
            using var image1=GetViewport().GetTexture().GetImage();using var crop1=image1.GetRegion(rect);byte[] pixels1=crop1.GetData();
            await ToSignal(GetTree().CreateTimer(.7),SceneTreeTimer.SignalName.Timeout);
            using var image2=GetViewport().GetTexture().GetImage();using var crop2=image2.GetRegion(rect);
            if(!pixels1.SequenceEqual(crop2.GetData()))throw new Exception("历史地块仍在播放动态");
            await PointerClick(_humanNav["end"]);
            await WaitFor(()=>S(_playingBeat?["phase"])=="battle","野人战斗未进入演出",HumanRoundTimeout);
            await ToSignal(GetTree(),SceneTree.SignalName.ProcessFrame);await ToSignal(GetTree(),SceneTree.SignalName.ProcessFrame);
            var combatWild=SquadView.Units(_map.Frame!).Where(u=>S(u["owner"])=="野人"&&I(u["x"])==destination.X&&I(u["y"])==destination.Y).ToArray();
            if(combatWild.Length!=1||I(combatWild[0]["id"])!=wildId||_map.WildFigureCounts.GetValueOrDefault(wildKey)!=wildFigures)throw new Exception("野人从驻军到战斗更换身份、编队或重复显示");
            if(_map.Frame!["scene"]!["guards"]!.AsArray().Any(u=>I(u!["x"])==destination.X&&I(u["y"])==destination.Y))throw new Exception("战场残留营地守军副本");
            var ledgerRect=_ledgerPanel.GetGlobalRect();
            var ledgerBounds=new Footprint(ledgerRect.Position.X,ledgerRect.Position.Y,ledgerRect.Size.X,ledgerRect.Size.Y);
            if(_map.SquadLabelBounds.Any(b=>(b with { Height=b.Height-35 }).Overlaps(ledgerBounds)))throw new Exception("战斗血条被国家面板挡住");
            await Screenshot(stem+"-wild-battle.png");await WaitFor(()=>HumanCanWrite&&_period==3,"战斗结算未结束",HumanRoundTimeout);
            _humanOperation="declare_war";await PointerClick(_humanNav["diplomacy"]);await PointerClick(_humanSubmit!);
            await WaitFor(()=>_humanConfirm.Visible,"宣战未显示二次确认");
            if(!_humanConfirm.DialogText.Contains("攻方")||!_humanConfirm.DialogText.Contains("守方"))throw new Exception("宣战缺少结构化警告");
            await Screenshot(stem+"-war-warning.png");await PointerClick(_humanConfirm.GetCancelButton());await HumanIdle();
            var layouts=new JsonArray();
            await ReviewHumanOutliner(stem,capital,destination);
            await ReviewResourceTooltip();await Screenshot(stem+"-resource-breakdown.png");
            await ReviewProductionQueue(stem,capital);
            await ReviewSpaceFilters(stem,capital);
            foreach(var size in new[]{new Vector2I(1920,1080),new Vector2I(1440,900),new Vector2I(1280,720),new Vector2I(1024,768)})
            {
                GetWindow().ContentScaleSize=size;GetWindow().Size=size;_humanCell=capital;OpenHumanPage("build");await RtsReady();
                await ToSignal(GetTree().CreateTimer(.2),SceneTreeTimer.SignalName.Timeout);ValidateHumanLayout();
                await Screenshot(stem+$"-{size.X}.png");
                foreach(string kind in new[]{"barracks","equipment-factory","castle"})
                {
                    await HoverRts(_rtsCards["native:"+kind]);await WaitFor(()=>_rtsExpanded,"长悬停未展开");
                    await ToSignal(GetTree(),SceneTree.SignalName.ProcessFrame);
                    if(!new Rect2(Vector2.Zero,Size).Encloses(_rtsTooltip!.GetGlobalRect()))throw new Exception("建筑悬浮信息超出窗口："+kind+" "+size);
                }
                await Screenshot(stem+$"-{size.X}-hover.png");
                HideRtsTooltip();OpenHumanPage("recruit");await RtsReady();ValidateHumanLayout();
                await HoverRts(_rtsCards["native:cavalry"]);await WaitFor(()=>_rtsExpanded,"兵种长悬停未展开");
                await ToSignal(GetTree(),SceneTree.SignalName.ProcessFrame);
                if(!new Rect2(Vector2.Zero,Size).Encloses(_rtsTooltip!.GetGlobalRect()))throw new Exception("征兵悬浮信息超出窗口："+size);
                HideRtsTooltip();_marketAutomatic=false;OpenHumanPage("market");await MarketReady();await ToSignal(GetTree().CreateTimer(.2),SceneTreeTimer.SignalName.Timeout);ValidateHumanLayout();await Screenshot(stem+$"-market-{size.X}.png");
                OpenHumanPage("production");await ToSignal(GetTree().CreateTimer(.2),SceneTreeTimer.SignalName.Timeout);ValidateHumanLayout();await Screenshot(stem+$"-production-queue-{size.X}.png");
                _marketAutomatic=false;OpenHumanPage("market");await MarketReady();
                await PointerClick((Button)_humanContent.FindChild("MarketAutoTab",true,false));ValidateHumanLayout();await Screenshot(stem+$"-auto-{size.X}.png");
                _humanCell=destination;OpenHumanPage("tile");await CheckGarrisonFirst();CheckOutlinerLayout();await Screenshot(stem+$"-garrison-{size.X}.png");
                OpenHumanOutliner(true);await WaitFor(()=>Drawers.All(d=>d.IsSettled),"总览打开时抽屉未收起");CheckOutlinerLayout();await Screenshot(stem+$"-outliner-{size.X}.png");
                await ReviewResourceTooltip();await Screenshot(stem+$"-resource-breakdown-{size.X}.png");
                layouts.Add(new JsonObject { ["width"]=size.X,["height"]=size.Y,["within_viewport"]=true,["rts_hover"]=true });
            }
            HideRtsTooltip();
            // Actual menu queues a save, then exposes explicit discard.
            await PointerClick(_save);await WaitFor(()=>_humanPaused,"真人 Esc 菜单没有暂停");
            await PointerClick(_menuSave);await WaitFor(()=>_hostState=="save-queued","真人回合中保存未排队");
            await PointerClick(_menuQuit);
            if(!_humanDiscard.Visible||!_pauseMenu.Visible||_closing)throw new Exception("真人退出没有保留选择");
            await Screenshot(stem+"-exit.png");
            var result=new JsonObject { ["period"]=_period,["human_actor"]=HumanActor,["human_actions"]=_humanHistory.Count,
                ["unknown_tiles"]=_map.UnknownCount,["historical_tiles"]=_map.MemoryCount,["action_batches"]=_actionBatches,
                ["settlement_batches"]=_settlementBatches,["played_phases"]=JsonSerializer.SerializeToNode(_playedPhases),["normal_zero_army_start"]=true,["repeated_market_clicks"]=true,["queued_order_cancel"]=true,["human_selected_speed"]=true,
                ["build_and_quote"]=true,["rts_icons"]=true,["two_step_player_menu"]=true,["custom_nation_name_and_color"]=true,["tile_a_b_a"]=true,["disabled_hover"]=true,["short_hover"]=true,["long_hover"]=true,["construction_quota_disabled"]=true,["letter_form"]=true,["free_human_scenario"]=true,["reject_multiple_humans"]=true,["recruit"]=true,["move_did_not_occupy"]=true,["in_place_attack"]=true,["esc_cancels_target"]=true,["war_warning"]=true,
                ["map_purchase"]=true,["historical_pixels_static"]=true,["single_nation_ledger"]=true,
                ["market_quantity_buttons"]=true,["market_exact_quotes"]=true,["auto_trade_buy_and_sell"]=true,["auto_trade_validation"]=true,["cancel_half_refund"]=true,["cancel_keeps_quota"]=true,["demolish_zero_refund"]=true,["market_layouts"]=true,
                ["garrison_visible_without_scroll"]=true,["wild_garrison_continuity"]=true,["wild_guard_id"]=wildId,["wild_formation_figures"]=wildFigures,
                ["resource_breakdown_hover"]=true,["outliner_tabs"]=true,["outliner_filters"]=true,["cross_site_selection"]=true,["mouse_orders"]=true,["shift_waypoints"]=true,["selection_halos"]=true,["numbers_without_enter"]=true,["market_reference_visible"]=true,["power_visible"]=true,["power_ignores_unfinished"]=true,["power_previous_actual"]=true,
                ["save_queued"]=true,["exit_choice"]=true,["rts_layouts"]=true,["save"]=_savePath,["config"]=configPath,["layouts"]=layouts,
                ["production_queue"]=true,["pending_full_refund"]=true,["march_order_badges"]=true,["rule_fixture_seed"]=20261026,
                ["simple_terrain_map"]=true,["terrain_preserves_fog"]=true,["terrain_price_steps"]=true,["free_space_filter"]=true,["compact_land_icons"]=true,["right_return_preferences"]=true,["guide_api_and_return"]=true,
                ["right_return_army_priority"]=true,
                ["right_return_last_button"]=true,
                ["platform"]=OS.GetName(),["renderer"]=RenderingServer.GetVideoAdapterName() };
            await File.WriteAllTextAsync(path+".json",result.ToJsonString(new JsonSerializerOptions { WriteIndented=true }));
            GD.Print("HUMAN_GUI_OK "+path);
            await PointerClick(_humanDiscard);await PointerClick(_humanConfirm.GetOkButton());
            await WaitFor(()=>_closing,"未确认放弃进度退出");
        }
        catch(Exception e)
        {
            GD.PrintErr(e);
            try{await Screenshot(Path.ChangeExtension(path,null)+"-failed.png");}catch{}
            if(_host!=null){SendHumanRow(new JsonObject { ["command"]="disconnect" });_closing=true;}
            GetTree().Quit(3);
        }
    }
}
