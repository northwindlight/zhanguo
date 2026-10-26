using Godot;
using System;
using System.IO;
using System.Linq;
using System.Collections.Generic;
using System.Text.Json;
using System.Text.Json.Nodes;
using System.Threading.Tasks;
using static WorldMap;

public partial class Desktop
{
    private void SelectHumanField(string field,string value)
    {
        var options=HumanFieldOptions(field)!;int index=options.FindIndex(o=>S(o.Value)==value);
        if(index<0)throw new Exception("验收选项不存在："+field+" "+value);
        var picker=_humanContent.FindChild("HumanField_"+field,true,false) as OptionButton;
        picker!.Select(index);picker.EmitSignal(OptionButton.SignalName.ItemSelected,(long)index);
    }
    private async Task HumanIdle()=>await WaitFor(()=>HumanCanWrite&&!HasTimeline,"真人操作或本批演出没有结束");
    private void ValidateHumanLayout()
    {
        var viewport=new Rect2(Vector2.Zero,Size);
        if(!viewport.Encloses(_humanDock.GetGlobalRect()))throw new Exception("真人底部栏超出窗口");
        foreach(var b in _humanNav.Values)if(!viewport.Encloses(b.GetGlobalRect()))throw new Exception("真人按钮超出窗口："+b.Text);
        if(_humanDrawer.IsOpen&&!viewport.Encloses(_humanDrawer.GetGlobalRect()))throw new Exception("真人抽屉超出窗口");
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
            _menu.OpenNewGame();_menu.SeedField.Text="20261026";_menu.ReviewHumanCard();
            await ToSignal(GetTree().CreateTimer(.3),SceneTreeTimer.SignalName.Timeout);
            _menu.ValidateVisibleLayout();await Screenshot(stem+"-menu.png");
            _menu.ReviewHumanCard(1);
            if(_menu.BuildConfig(out string multiError,out _)!=null||!multiError.Contains("一位真人"))throw new Exception("菜单未阻止多真人席位");
            _menu.SelectScenario(1);_menu.ReviewHumanCard();
            var freeHuman=_menu.BuildConfig(out string freeError,out _);
            if(freeHuman==null||freeHuman["scenario"]!=null||freeHuman["nations"]!.AsArray().Count(n=>S(n!["client"])=="human")!=1)throw new Exception("自由剧本真人配置错误："+freeError);
            _menu.SelectScenario(0);_menu.ReviewHumanCard();
            var config=_menu.BuildConfig(out string error,out string? configPath);
            if(config==null||configPath==null||config["nations"]!.AsArray().Count(n=>S(n!["client"])=="human")!=1)throw new Exception("真人菜单配置失败："+error);
            _menu.Visible=false;StartHost(configPath,null);
            await HumanIdle();if(OwnArmies.Any())throw new Exception("正常真人开局不应赠兵");CloseDrawers(false);_map.Fit();await Screenshot(path);
            foreach(string page in new[]{"build","army","market","diplomacy","alliance","intel","nation"})
            {
                await PointerClick(_humanNav[page]);await ToSignal(GetTree(),SceneTree.SignalName.ProcessFrame);
                ValidateHumanLayout();await Screenshot(stem+"-"+page+".png");
            }
            // A real double pointer click must buy one unit once.
            await PointerClick(_humanNav["market"]);double food=F(HumanFacts["econ"]!["stocks"]!.AsArray().First(s=>S(s!["id"])=="native:food")!["amount"]);
            int history=_humanHistory.Count;
            await PointerClick(_humanSubmit!);await PointerClick(_humanSubmit!);await HumanIdle();
            if(F(HumanFacts["econ"]!["stocks"]!.AsArray().First(s=>S(s!["id"])=="native:food")!["amount"])!=food+1||_humanHistory.Count!=history+1)
                throw new Exception("市场双击重复执行或成交未同步");
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
            OpenHumanPage("build");SelectHumanField("kind","native:militia-camp");RequestHumanPreview();
            await WaitFor(()=>_humanPreview!.Text.Contains("实际材料"),"建设预览没有收到真实费用");
            await Screenshot(stem+"-quote.png");await PointerClick(_humanSubmit!);await HumanIdle();
            if(!HumanFacts["building_sites"]!["instances"]!.AsArray().Any(b=>S(b!["kind"])=="native:militia-camp"&&I(b["remaining"])==1))throw new Exception("建设没有真实动工");
            await PointerClick(_humanNav["end"]);
            await WaitFor(()=>S(_playingBeat?["phase"])=="production","本国生产没有演出");
            if(!_settlementCard.Visible||_humanDrawer.IsOpen)throw new Exception("生产汇总被操作面板遮住");
            await Screenshot(stem+"-production.png");
            await WaitFor(()=>HumanCanWrite&&_period==1,"真人回合没有轮换与结算");
            _humanCell=capital;_humanOperation="recruit";OpenHumanPage("recruit");SelectHumanField("kind","native:militia");
            await PointerClick(_humanSubmit!);await HumanIdle();
            if(!OwnArmies.Any())throw new Exception("真人征兵没有产生真实军队："+_humanReceipt.Text);
            await PointerClick(_humanNav["end"]);await WaitFor(()=>HumanCanWrite&&_period==2,"新兵没有进入下一期");
            _humanCell=capital;OpenHumanPage("tile");
            var check=_humanContent.FindChild("HumanArmy1",true,false) as CheckButton;
            if(check==null)throw new Exception("本国驻军没有复选框");await PointerClick(check);
            StartHumanTarget("move");await WaitFor(()=>_map.TargetCount>0,"没有逐格移动目标圈");
            await Screenshot(stem+"-move.png");
            GetViewport().PushInput(new InputEventKey { Keycode=Key.Escape,Pressed=true },true);
            if(_map.TargetCount!=0||_pauseMenu.Visible)throw new Exception("Esc 未优先取消军令");
            StartHumanTarget("move");await WaitFor(()=>_map.TargetCount>0,"取消后没有恢复军令范围");
            var target=_map.Frame!["tiles"]!.AsArray().First(t=>S(t!["visibility"])=="live"&&t["owner"]==null&&
                _map.HumanTarget(new(I(t["position"]![0]),I(t["position"]![1])))!=null)!;
            var destination=new Vector2I(I(target["position"]![0]),I(target["position"]![1]));
            await PointerClickAt(_map.CellCenter(destination));await HumanIdle();
            if(!OwnArmies.Any(u=>I(u["x"])==destination.X&&I(u["y"])==destination.Y)||S(_map.Frame["tiles"]!.AsArray().First(t=>I(t!["position"]![0])==destination.X&&I(t["position"]![1])==destination.Y)!["owner"])!="")
                throw new Exception("移动没有执行或被错误转成占领");
            _humanCell=destination;OpenHumanPage("tile");StartHumanTarget("attack");await WaitFor(()=>_map.TargetCount>0,"原地攻击没有独立准星圈");
            await Screenshot(stem+"-attack.png");await PointerClickAt(_map.CellCenter(destination));await HumanIdle();
            if(!OwnArmies.Any(u=>u["engaged"]?.GetValue<bool>()==true))throw new Exception("进攻没有按真实规则登记交战");
            _humanOperation="buy_map";await PointerClick(_humanNav["intel"]);await PointerClick(_humanSubmit!);await HumanIdle();
            if(_map.MemoryCount==0)throw new Exception("购图没有合入历史地图");
            var memory=_map.Frame!["tiles"]!.AsArray().First(t=>S(t!["visibility"])=="memory")!;
            var memoryCell=new Vector2I(I(memory["position"]![0]),I(memory["position"]![1]));
            CloseDrawers(false);_map.SelectAndFocus(memoryCell);await Screenshot(stem+"-memory.png");
            var center=_map.CellCenter(memoryCell);int radius=(int)Math.Max(8,48*_map.Zoom);
            var rect=new Rect2I((int)center.X-radius,(int)center.Y-radius,radius*2,radius*2);
            using var image1=GetViewport().GetTexture().GetImage();using var crop1=image1.GetRegion(rect);byte[] pixels1=crop1.GetData();
            await ToSignal(GetTree().CreateTimer(.7),SceneTreeTimer.SignalName.Timeout);
            using var image2=GetViewport().GetTexture().GetImage();using var crop2=image2.GetRegion(rect);
            if(!pixels1.SequenceEqual(crop2.GetData()))throw new Exception("历史地块仍在播放动态");
            await PointerClick(_humanNav["end"]);await WaitFor(()=>HumanCanWrite&&_period==3,"战斗结算未结束");
            _humanOperation="declare_war";await PointerClick(_humanNav["diplomacy"]);await PointerClick(_humanSubmit!);
            await WaitFor(()=>_humanConfirm.Visible,"宣战未显示二次确认");
            if(!_humanConfirm.DialogText.Contains("攻方")||!_humanConfirm.DialogText.Contains("守方"))throw new Exception("宣战缺少结构化警告");
            await Screenshot(stem+"-war-warning.png");await PointerClick(_humanConfirm.GetCancelButton());await HumanIdle();
            var layouts=new JsonArray();
            foreach(var size in new[]{new Vector2I(1920,1080),new Vector2I(1440,900),new Vector2I(1280,720),new Vector2I(1024,768)})
            {
                GetWindow().ContentScaleSize=size;GetWindow().Size=size;OpenHumanPage("diplomacy");
                await ToSignal(GetTree().CreateTimer(.2),SceneTreeTimer.SignalName.Timeout);ValidateHumanLayout();
                await Screenshot(stem+$"-{size.X}.png");layouts.Add(new JsonObject { ["width"]=size.X,["height"]=size.Y,["within_viewport"]=true });
            }
            // Actual menu queues a save, then exposes explicit discard.
            await PointerClick(_save);await WaitFor(()=>_humanPaused,"真人 Esc 菜单没有暂停");
            await PointerClick(_menuSave);await WaitFor(()=>_hostState=="save-queued","真人回合中保存未排队");
            await PointerClick(_menuQuit);
            if(!_humanDiscard.Visible||!_pauseMenu.Visible||_closing)throw new Exception("真人退出没有保留选择");
            await Screenshot(stem+"-exit.png");
            var result=new JsonObject { ["period"]=_period,["human_actor"]=HumanActor,["human_actions"]=_humanHistory.Count,
                ["unknown_tiles"]=_map.UnknownCount,["historical_tiles"]=_map.MemoryCount,["action_batches"]=_actionBatches,
                ["settlement_batches"]=_settlementBatches,["played_phases"]=JsonSerializer.SerializeToNode(_playedPhases),["normal_zero_army_start"]=true,["double_click_once"]=true,
                ["build_and_quote"]=true,["letter_form"]=true,["free_human_scenario"]=true,["reject_multiple_humans"]=true,["recruit"]=true,["move_did_not_occupy"]=true,["in_place_attack"]=true,["esc_cancels_target"]=true,["war_warning"]=true,
                ["map_purchase"]=true,["historical_pixels_static"]=true,["single_nation_ledger"]=true,
                ["save_queued"]=true,["exit_choice"]=true,["save"]=_savePath,["config"]=configPath,["layouts"]=layouts,
                ["platform"]=OS.GetName(),["renderer"]=RenderingServer.GetVideoAdapterName() };
            await File.WriteAllTextAsync(path+".json",result.ToJsonString(new JsonSerializerOptions { WriteIndented=true }));
            GD.Print("HUMAN_GUI_OK "+path);
            await PointerClick(_humanDiscard);await PointerClick(_humanConfirm.GetOkButton());
            await WaitFor(()=>_closing,"未确认放弃进度退出");
        }
        catch(Exception e)
        {
            GD.PrintErr(e);
            if(_host!=null){SendHumanRow(new JsonObject { ["command"]="disconnect" });_closing=true;}
            GetTree().Quit(3);
        }
    }
}
