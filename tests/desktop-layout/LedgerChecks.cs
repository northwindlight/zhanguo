using System;
using System.Linq;
using System.Text.Json.Nodes;
using Zhanguo.Desktop.Layout;

static class LedgerChecks
{
    private static void Require(bool value,string why){if(!value)throw new Exception(why);}
    public static void Run()
    {
        var frame=JsonNode.Parse("""
            {"period":4,"tiles":[],"nations":[{"id":"q","name":"秦","stocks":[],"units":{}},{"id":"c","name":"楚","stocks":[],"units":{}}],
             "scene":{"buildings":[],"ownership":[],"armies":[],"market":[],
                "accounts":[{"id":"q","stocks":[{"id":"native:gold","name":"金币","amount":100}]},{"id":"c","stocks":[{"id":"native:gold","name":"金币","amount":300}]}],
                "resource_flows":[{"id":"q","period":3,"net":[["native:gold",12]]},{"id":"c","period":3,"net":[["native:gold",-8]]}]}}
            """)!.AsObject();
        DesktopScene.Synchronize(frame);
        var before=frame.ToJsonString();var q=NationLedger.Read(frame,"q")!;var c=NationLedger.Read(frame,"c")!;
        Require(q.Name=="秦"&&q.Stocks.Single().Amount==100&&q.Stocks.Single().Change==12&&q.FlowPeriod==3,"single-country ledger mixed holdings, flow or country");
        Require(c.Name=="楚"&&c.Stocks.Single().Amount==300&&c.Stocks.Single().Change==-8,"country switching retained another owner's data");
        Require(before==frame.ToJsonString()&&NationLedger.Read(frame,"missing")==null,"ledger read mutated facts or invented a country");
        var trade=JsonNode.Parse("""{"accounts":{"remove":[],"upsert":[{"id":"q","stocks":[{"id":"native:gold","name":"金币","amount":80}]}]}}""")!.AsObject();
        DesktopScene.Apply(frame,trade);q=NationLedger.Read(frame,"q")!;
        Require(q.Stocks.Single().Amount==80&&q.Stocks.Single().Change==12&&q.FlowPeriod==3,"trade was mislabeled as production or pending settlement was shown early");
        var settled=JsonNode.Parse("""{"resource_flows":{"remove":[],"upsert":[{"id":"q","period":4,"net":[]}]}}""")!.AsObject();
        DesktopScene.Apply(frame,settled);q=NationLedger.Read(frame,"q")!;
        Require(q.FlowPeriod==4&&q.Stocks.Single().Change==0&&NationLedger.Read(frame,"c")!.FlowPeriod==3,"zero flow remained stale or another country's settlement advanced early");
        Require(NationLedger.Signed(-8)=="-8"&&NationLedger.Signed(12)=="+12"&&NationLedger.Signed(0)=="—","signed resource labels lost their sign");
        var grid=JsonNode.Parse("""{"power":{"remove":[],"upsert":[{"id":"q","capacity":4,"generated":2,"demand":3,"previous_short":true,"fuel_short_plants":1}]}}""")!.AsObject();
        DesktopScene.Apply(frame,grid);q=NationLedger.Read(frame,"q")!;
        Require(q.Power is {Capacity:4,Generated:2,Demand:3,PreviousShort:true,FuelShortPlants:1},"ledger confused rated capacity, current fuel-limited supply and prior actual grid");
        Require(NationLedger.Read(frame,"c")!.Power==null,"another country's power leaked into this country's ledger");
        Console.WriteLine("NATION_LEDGER_OK: one country, actual action-time holdings, dated flows, zero reset and detached projection");
    }
}
