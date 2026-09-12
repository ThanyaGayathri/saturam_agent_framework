// OnboardingModal - Auto-generated from Figma
// Figma node: 18349:319866
// Screen name: Landing Page
// Screenshot reference: ../screenshots/LandingPage_05.png

import React from "react";
import logoSvg from "../assets/retailgpt-logo.svg";
import snowflakeSvg from "../assets/snowflake.svg";
import shopifySvg from "../assets/shopify.svg";
import salesforceSvg from "../assets/salesforce.svg";

export default function OnboardingModal() {
  return (
    <div className="flex flex-col justify-start items-start w-full min-h-screen bg-surface">
      <div className="flex flex-col justify-center items-center p-[32px] w-[1680px] h-[1236px] overflow-hidden">
        <div className="w-[1680px] h-[1236px]">
          <div className="w-[1680px] h-[1236px] overflow-hidden bg-[#0a0c12]" />
        </div>
        <div className="flex flex-col justify-start items-center rounded-2xl w-full shadow-[0px_3px_3px_rgba(10_12_18_0.04)] shadow-[0px_8px_8px_rgba(10_12_18_0.03)] shadow-[0px_20px_24px_rgba(10_12_18_0.08)] overflow-hidden">
          <div className="flex flex-col justify-start items-center gap-6 pt-[32px] px-[32px] w-full">
            <div className="flex flex-col justify-start items-center gap-3 w-full">
              <h3 className="text-2xl font-semibold leading-[32px] text-center text-text-primary">Connect your Enterprise Intelligence</h3>
              <p className="text-base font-normal leading-[24px] text-center text-[#535861]">Connect your data to get a more personalized experience that:</p>
            </div>
            <div className="rounded-lg w-full h-[220px] overflow-hidden bg-[#f5f5f5]">
              <div className="w-[260px] h-[146px]" />
              <div className="flex flex-col justify-center items-center gap-[13.360791206359863px] p-12 rounded-xl shadow-[0px_0px_1px_rgba(10_12_18_0.05)]">
                <div className="w-[42px] h-[42px] overflow-hidden" />
              </div>
              <div className="flex flex-col justify-center items-center gap-[13.360791206359863px] p-12 rounded-xl shadow-[0px_0px_1px_rgba(10_12_18_0.05)]">
                <div className="w-[42px] h-[42px] overflow-hidden" />
              </div>
              <div className="flex flex-col justify-center items-center gap-[13.360791206359863px] p-12 rounded-xl shadow-[0px_0px_1px_rgba(10_12_18_0.05)]">
                <div className="w-[42px] h-[42px] overflow-hidden" />
              </div>
              <div className="flex flex-col justify-center items-center gap-[13.360791206359863px] p-12 rounded-xl shadow-[0px_0px_1px_rgba(10_12_18_0.05)]">
                <div className="w-[42px] h-[42px] overflow-hidden" />
              </div>
              <div className="flex flex-col justify-center items-center gap-[13.360791206359863px] p-12 rounded-xl shadow-[0px_0px_1px_rgba(10_12_18_0.05)]">
                  <div className="rounded-lg w-full h-full overflow-hidden">
                    <div className="w-[38px] h-[38px] bg-[#2a2a37] rounded" />
                  </div>
              </div>
            </div>
          </div>
              <div className="flex flex-col justify-start items-start gap-3 w-full">
                <div className="flex justify-start items-center gap-3 w-full">
                  <div className="flex flex-col justify-center items-center gap-[13.360791206359863px] p-8 rounded-[10.0px] w-[48px] h-[48px] shadow-[0px_0px_1px_rgba(10_12_18_0.05)]">
                    <img src={snowflakeSvg} alt="" className="h-6 w-6 object-contain" />
                  </div>
                  <div className="flex flex-col justify-start items-start w-full">
                    <p className="text-base font-semibold leading-[24px] text-text-primary">Unify Data</p>
                    <p className="text-sm font-normal leading-[20px] text-[#535861]">Link Snowflake or BigQuery to give the AI long-term memory.</p>
                  </div>
                </div>
                <div className="flex justify-start items-center gap-3 w-full">
                  <div className="flex flex-col justify-center items-center gap-[13.360791206359863px] p-8 rounded-[10.0px] w-[48px] h-[48px] shadow-[0px_0px_1px_rgba(10_12_18_0.05)]">
                    <img src={shopifySvg} alt="" className="h-6 w-6 object-contain" />
                  </div>
                  <div className="flex flex-col justify-start items-start w-full">
                    <p className="text-base font-semibold leading-[24px] text-text-primary">Enterprise Integrations</p>
                    <p className="text-sm font-normal leading-[20px] text-[#535861]">Sync real-time signals from Shopify & SAP to track inventory & orders.</p>
                  </div>
                </div>
                <div className="flex justify-start items-center gap-3 w-full">
                  <div className="flex flex-col justify-center items-center gap-[13.360791206359863px] p-8 rounded-[10.0px] w-[48px] h-[48px] shadow-[0px_0px_1px_rgba(10_12_18_0.05)]">
                    <img src={salesforceSvg} alt="" className="h-6 w-6 object-contain" />
                  </div>
                  <div className="flex flex-col justify-start items-start w-full">
                    <p className="text-base font-semibold leading-[24px] text-text-primary">Actionable Insights</p>
                    <p className="text-sm font-normal leading-[20px] text-[#535861]">Transform metrics into Live Dashboards and auto-generated plans.</p>
                  </div>
                </div>
              </div>
            <div className="flex flex-col justify-center items-center gap-3 pb-[24px] px-[32px] w-full">
              <div className="flex justify-center items-center gap-1.5 py-10 px-16 rounded-lg w-full shadow-sm overflow-hidden bg-[#6840c6]">
                  <p className="text-base font-semibold leading-[24px] text-[#ffffff]">Connect Database</p>
                <div className="w-[20px] h-[20px] overflow-hidden" />
              </div>
              <div className="flex justify-center items-center gap-1.5 py-8 px-16 w-full overflow-hidden">
                <p className="text-base font-semibold leading-[24px] text-[#535861]">I’ll do it later</p>
              </div>
            </div>
        </div>
      </div>
      <div className="flex flex-col justify-start items-center w-full border border-border overflow-hidden">
        <div className="flex justify-between items-center gap-[738.0px] px-[32px] w-full h-[72px]">
              <div className="flex justify-start items-center gap-1">
                  <img src={logoSvg} alt="" className="h-8 w-8 rounded-lg" />
                  <p className="text-xl font-semibold leading-[28px] tracking-[-0.8px] text-[#2a2a37]">EnterpriseGPT</p>
              </div>
          <div className="flex justify-start items-center gap-0.5">
            <div className="flex justify-start items-center py-2 overflow-hidden">
              <div className="flex justify-start items-center gap-3 py-8 px-12 rounded-md bg-surface-secondary">
                  <p className="text-base font-semibold leading-[24px] text-text-primary">Playground</p>
              </div>
            </div>
            <div className="flex justify-start items-center py-2 overflow-hidden">
              <div className="flex justify-start items-center gap-3 py-8 px-12 rounded-md">
                  <p className="text-base font-semibold leading-[24px] text-text-tertiary">Configure</p>
              </div>
            </div>
            <div className="flex justify-start items-center py-2 overflow-hidden">
              <div className="flex justify-start items-center gap-3 py-8 px-12 rounded-md">
                  <p className="text-base font-semibold leading-[24px] text-text-tertiary">Use Cases</p>
              </div>
            </div>
            <div className="flex justify-start items-center py-2 overflow-hidden">
              <div className="flex justify-start items-center gap-3 py-8 px-12 rounded-md">
                  <p className="text-base font-semibold leading-[24px] text-text-tertiary">KPIs</p>
              </div>
            </div>
            <div className="flex justify-start items-center py-2 overflow-hidden">
              <div className="flex justify-start items-center gap-3 py-8 px-12 rounded-md">
                  <p className="text-base font-semibold leading-[24px] text-text-tertiary">Processes</p>
              </div>
            </div>
            <div className="flex justify-start items-center py-2 overflow-hidden">
              <div className="flex justify-start items-center gap-3 py-8 px-12 rounded-md">
                  <p className="text-base font-semibold leading-[24px] text-text-tertiary">Integrations</p>
              </div>
            </div>
            <div className="flex justify-start items-center py-2 overflow-hidden">
              <div className="flex justify-start items-center gap-3 py-8 px-12 rounded-md">
                  <p className="text-base font-semibold leading-[24px] text-text-tertiary">Monitor</p>
              </div>
            </div>
            <div className="flex justify-start items-center py-2 overflow-hidden">
              <div className="flex justify-start items-center gap-3 py-8 px-12 rounded-md">
                  <p className="text-base font-semibold leading-[24px] text-text-tertiary">Alerts</p>
              </div>
            </div>
          </div>
          <div className="flex justify-start items-center gap-3">
            <div className="flex justify-start items-start gap-0.5">
              <div className="flex justify-center items-center p-8 rounded-md w-[40px] overflow-hidden">
                <div className="w-[20px] h-[20px] overflow-hidden" />
              </div>
              <div className="flex justify-center items-center p-8 rounded-md w-[40px] overflow-hidden">
                <div className="w-[20px] h-[20px] overflow-hidden" />
              </div>
            </div>
              <div className="rounded-full w-[40px] h-[40px] border border-[rgba(0_0_0_0.08)] overflow-hidden" />
          </div>
        </div>
      </div>
      <div className="flex flex-col justify-start items-center gap-8 pb-[72px] w-full">
        <div className="flex flex-col justify-start items-center gap-6 pt-[168px] pb-[64px] px-24 w-full">
            <h2 className="text-4xl font-normal leading-[44px] tracking-[-0.7px] text-center text-text-primary">Unlock Enterprise Use-Cases with EnterpriseGPT</h2>
          <div className="flex flex-col justify-start items-start gap-2 rounded-2xl w-[768px]">
            <div className="flex flex-col justify-center items-start gap-3 py-12 px-[18px] rounded-2xl w-full overflow-hidden bg-[rgba(246_246_246_0.50)]">
              <div className="flex flex-col justify-center items-start gap-1 px-6 rounded-lg w-full h-[52px]">
                <p className="text-base font-normal leading-[24px] text-text-primary">Ask anything</p>
              </div>
              <div className="flex justify-between items-center gap-2 rounded-lg w-full">
                <div className="flex justify-start items-center gap-1 rounded-lg">
                  <div className="flex justify-center items-center gap-1 py-4 px-12 rounded-xl w-[40px]">
                    <div className="w-[18px] h-[18px] overflow-hidden" />
                  </div>
                  <div className="flex justify-center items-center gap-1 py-4 px-12 rounded-xl w-[40px]">
                    <div className="w-[18px] h-[18px] overflow-hidden" />
                  </div>
                  <div className="flex justify-center items-center gap-2 py-4 px-12 rounded-xl">
                    <div className="w-[18px] h-[18px] overflow-hidden" />
                    <p className="text-base font-normal leading-[24px] text-text-muted">1 of 10</p>
                  </div>
                </div>
                <div className="flex justify-start items-center gap-2">
                  <div className="flex justify-center items-center gap-2 p-12 rounded-[99.0px] w-[32px] h-[32px]">
                    <div className="w-[24px] h-[24px] overflow-hidden" />
                  </div>
                  <div className="flex justify-center items-center gap-2 p-12 rounded-[99.0px] w-[32px] h-[32px]">
                    <div className="w-[24px] h-[24px]" />
                  </div>
                </div>
              </div>
            </div>
            <p className="text-sm font-normal leading-[20px] text-center text-[#535861]">AI-generated insights are based on connected data sources. Critical business decisions should be verified by domain experts.</p>
          </div>
            <div className="flex justify-center items-start gap-2.5 w-full">
              <div className="flex justify-center items-center gap-1.5 py-12 px-20 rounded-xl border border-[rgba(140_148_171_0.10)] overflow-hidden bg-[#eff2ff]">
                <div className="w-[20px] h-[20px] overflow-hidden" />
                  <p className="text-base font-normal leading-[24px] text-[#535861]">Inventory</p>
              </div>
              <div className="flex justify-center items-center gap-1.5 py-12 px-20 rounded-xl border border-[rgba(140_148_171_0.10)] overflow-hidden bg-[#eff2ff]">
                <div className="w-[20px] h-[20px] overflow-hidden" />
                  <p className="text-base font-normal leading-[24px] text-[#535861]">Orders</p>
              </div>
              <div className="flex justify-center items-center gap-1.5 py-12 px-20 rounded-xl border border-[rgba(140_148_171_0.10)] overflow-hidden bg-[#eff2ff]">
                <div className="w-[20px] h-[20px] overflow-hidden" />
                  <p className="text-base font-normal leading-[24px] text-[#535861]">Merchandise</p>
              </div>
              <div className="flex justify-center items-center gap-1.5 py-12 px-20 rounded-xl border border-[rgba(140_148_171_0.10)] overflow-hidden bg-[#eff2ff]">
                <div className="w-[20px] h-[20px] overflow-hidden" />
                  <p className="text-base font-normal leading-[24px] text-[#535861]">Store Ops</p>
              </div>
              <div className="flex justify-center items-center gap-1.5 py-12 px-20 rounded-xl border border-[rgba(140_148_171_0.10)] overflow-hidden bg-[#eff2ff]">
                <div className="w-[20px] h-[20px] overflow-hidden" />
                  <p className="text-base font-normal leading-[24px] text-[#535861]">E-Com</p>
              </div>
              <div className="flex justify-center items-center gap-1.5 py-12 px-20 rounded-xl border border-[rgba(140_148_171_0.10)] overflow-hidden bg-[#eff2ff]">
                <div className="w-[20px] h-[20px] overflow-hidden" />
                  <p className="text-base font-normal leading-[24px] text-[#535861]">POS</p>
              </div>
              <div className="flex justify-center items-center gap-1.5 py-12 px-20 rounded-xl border border-[rgba(140_148_171_0.10)] overflow-hidden bg-[#eff2ff]">
                <div className="w-[20px] h-[20px] overflow-hidden" />
                  <p className="text-base font-normal leading-[24px] text-[#535861]">Support</p>
              </div>
              <div className="flex justify-center items-center gap-1.5 py-12 px-20 rounded-xl border border-[rgba(140_148_171_0.15)] overflow-hidden">
                  <p className="text-base font-normal leading-[24px] text-[#535861]">See more</p>
                <div className="w-[24px] h-[24px] overflow-hidden" />
              </div>
            </div>
        </div>
        <div className="flex flex-col justify-start items-center gap-6 w-full overflow-hidden">
              <div className="flex justify-start items-start gap-4 w-full">
                  <p className="text-lg font-medium leading-[28px] text-text-primary">What do you need help with?</p>
                  <div className="flex justify-center items-center gap-1 py-8 px-12 rounded-lg border border-[#d5d6d9] overflow-hidden">
                      <p className="text-sm font-semibold leading-[20px] text-text-tertiary">Browse the Use Cases</p>
                    <div className="w-[20px] h-[20px] overflow-hidden" />
                  </div>
              </div>
                  <div className="flex justify-start items-center gap-6 w-full">
                    <div className="flex flex-col justify-start items-start gap-6 p-24 rounded-2xl w-[326px] h-[412px] bg-[rgba(254_254_254_0.05)]" style={{ backgroundImage: "linear-gradient(135deg, #e8e8e8 0.0%, #c2c2c2 100.0%)" }}>
                      <div className="rounded-[10.0px] w-[48px] h-[48px] overflow-hidden" style={{ backgroundImage: "linear-gradient(135deg, #31bbff 0.0%, #9676ff 25.0%, #be64fe 41.2%, #e157cb 51.8%, #ef5794 62.1%, #fd683f 69.7%, #fe7c2b 85.2%, #ffa10b 100.0%)" }}>
                        <div className="w-[24px] h-[24px] overflow-hidden" />
                      </div>
                      <div className="flex flex-col justify-start items-start gap-4 w-full">
                        <div className="flex flex-col justify-start items-start gap-4 w-full">
                          <p className="text-lg font-semibold leading-[28px] text-text-primary">E-Com</p>
                          <p className="text-base font-normal leading-[24px] text-[#535861]">Cart abandonment rise for weekend traffic
Low conversion from traffic source “Instagram Ads”
Product page load time increased for category “Beauty”</p>
                          <div className="w-[53px] h-[134px]">
                            <div className="w-[16px] h-[16px] overflow-hidden" />
                            <div className="w-[16px] h-[16px] overflow-hidden" />
                            <div className="w-[16px] h-[16px] overflow-hidden" />
                          </div>
                        </div>
                        <div className="flex justify-center items-center gap-1.5 overflow-hidden">
                          <p className="text-base font-semibold leading-[24px] text-primary">Convert</p>
                          <div className="w-[20px] h-[20px] overflow-hidden" />
                        </div>
                      </div>
                    </div>
                    <div className="flex flex-col justify-start items-start gap-6 p-24 rounded-2xl w-[326px] h-[412px] bg-[rgba(254_254_254_0.05)]" style={{ backgroundImage: "linear-gradient(135deg, #e8e8e8 0.0%, #c2c2c2 100.0%)" }}>
                      <div className="rounded-[10.0px] w-[48px] h-[48px] overflow-hidden" style={{ backgroundImage: "linear-gradient(135deg, #31bbff 0.0%, #9676ff 25.0%, #be64fe 41.2%, #e157cb 51.8%, #ef5794 62.1%, #fd683f 69.7%, #fe7c2b 85.2%, #ffa10b 100.0%)" }}>
                        <div className="w-[24px] h-[24px] overflow-hidden" />
                      </div>
                      <div className="flex flex-col justify-start items-start gap-4 w-full">
                        <div className="flex flex-col justify-start items-start gap-4 w-full">
                          <p className="text-lg font-semibold leading-[28px] text-text-primary">POS</p>
                          <p className="text-base font-normal leading-[24px] text-[#535861]">POS transaction failures &gt;5% at store “LA Downtown”
Long checkout queue detected (avg wait &gt;10 min)
Low attachment rate for add-on products</p>
                          <div className="w-[100px] h-[132px]">
                            <div className="w-[16px] h-[16px] overflow-hidden" />
                            <div className="w-[16px] h-[16px] overflow-hidden" />
                            <div className="w-[16px] h-[16px] overflow-hidden" />
                          </div>
                        </div>
                        <div className="flex justify-center items-center gap-1.5 overflow-hidden">
                          <p className="text-base font-semibold leading-[24px] text-primary">Improve</p>
                          <div className="w-[20px] h-[20px] overflow-hidden" />
                        </div>
                      </div>
                    </div>
                    <div className="flex flex-col justify-start items-start gap-6 p-24 rounded-2xl w-[326px] h-[412px] bg-[rgba(254_254_254_0.05)]" style={{ backgroundImage: "linear-gradient(135deg, #e8e8e8 0.0%, #c2c2c2 100.0%)" }}>
                      <div className="rounded-[10.0px] w-[48px] h-[48px] overflow-hidden" style={{ backgroundImage: "linear-gradient(135deg, #31bbff 0.0%, #9676ff 25.0%, #be64fe 41.2%, #e157cb 51.8%, #ef5794 62.1%, #fd683f 69.7%, #fe7c2b 85.2%, #ffa10b 100.0%)" }}>
                        <div className="w-[24px] h-[24px] overflow-hidden" />
                      </div>
                      <div className="flex flex-col justify-start items-start gap-6 w-full">
                        <div className="flex flex-col justify-start items-start gap-4 w-full">
                          <p className="text-lg font-semibold leading-[28px] text-text-primary">Store Ops</p>
                          <p className="text-base font-normal leading-[24px] text-[#535861]">Demand not being addressed for region “North East”
Staffing not complete for store ”Decatur-ATL” for Christmas season
Low customer flow expected in region “East” in January</p>
                          <div className="w-[135px] h-[155px]">
                            <div className="w-[16px] h-[16px] overflow-hidden" />
                            <div className="w-[16px] h-[16px] overflow-hidden" />
                            <div className="w-[16px] h-[16px] overflow-hidden" />
                          </div>
                        </div>
                        <div className="flex justify-center items-center gap-1.5 overflow-hidden">
                          <p className="text-base font-semibold leading-[24px] text-primary">Analyze</p>
                          <div className="w-[20px] h-[20px] overflow-hidden" />
                        </div>
                      </div>
                    </div>
                    <div className="flex flex-col justify-start items-start gap-6 p-24 rounded-2xl w-[326px] h-[412px] bg-[rgba(254_254_254_0.05)]" style={{ backgroundImage: "linear-gradient(135deg, #e8e8e8 0.0%, #c2c2c2 100.0%)" }}>
                      <div className="rounded-[10.0px] w-[48px] h-[48px] overflow-hidden" style={{ backgroundImage: "linear-gradient(135deg, #31bbff 0.0%, #9676ff 25.0%, #be64fe 41.2%, #e157cb 51.8%, #ef5794 62.1%, #fd683f 69.7%, #fe7c2b 85.2%, #ffa10b 100.0%)" }}>
                        <div className="w-[24px] h-[24px] overflow-hidden" />
                      </div>
                      <div className="flex flex-col justify-start items-start gap-4 w-full">
                        <div className="flex flex-col justify-start items-start gap-4 w-full">
                          <p className="text-lg font-semibold leading-[28px] text-text-primary">Support</p>
                          <p className="text-base font-normal leading-[24px] text-[#535861]">Surge in unresolved customer calls for product returns
Spike in average wait time during weekend peaks
Increased call drop rate from region “Midwest”</p>
                          <div className="w-[37px] h-[132px]">
                            <div className="w-[16px] h-[16px] overflow-hidden" />
                            <div className="w-[16px] h-[16px] overflow-hidden" />
                            <div className="w-[16px] h-[16px] overflow-hidden" />
                          </div>
                        </div>
                        <div className="flex justify-center items-center gap-1.5 overflow-hidden">
                          <p className="text-base font-semibold leading-[24px] text-primary">Diagnose</p>
                          <div className="w-[20px] h-[20px] overflow-hidden" />
                        </div>
                      </div>
                    </div>
                    <div className="flex flex-col justify-start items-start gap-6 p-24 rounded-2xl w-[326px] h-[412px] bg-[rgba(254_254_254_0.05)]" style={{ backgroundImage: "linear-gradient(135deg, #e8e8e8 0.0%, #c2c2c2 100.0%)" }}>
                      <div className="rounded-[10.0px] w-[48px] h-[48px] overflow-hidden" style={{ backgroundImage: "linear-gradient(135deg, #31bbff 0.0%, #9676ff 25.0%, #be64fe 41.2%, #e157cb 51.8%, #ef5794 62.1%, #fd683f 69.7%, #fe7c2b 85.2%, #ffa10b 100.0%)" }}>
                        <div className="w-[24px] h-[24px] overflow-hidden" />
                      </div>
                      <div className="flex flex-col justify-start items-start gap-4 w-full">
                        <div className="flex flex-col justify-start items-start gap-4 w-full">
                          <p className="text-lg font-semibold leading-[28px] text-text-primary">Support</p>
                          <p className="text-base font-normal leading-[24px] text-[#535861]">Surge in unresolved customer calls for product returns
Spike in average wait time during weekend peaks
Increased call drop rate from region “Midwest”</p>
                          <div className="w-[37px] h-[132px]">
                            <div className="w-[16px] h-[16px] overflow-hidden" />
                            <div className="w-[16px] h-[16px] overflow-hidden" />
                            <div className="w-[16px] h-[16px] overflow-hidden" />
                          </div>
                        </div>
                        <div className="flex justify-center items-center gap-1.5 overflow-hidden">
                          <p className="text-base font-semibold leading-[24px] text-primary">Diagnose</p>
                          <div className="w-[20px] h-[20px] overflow-hidden" />
                        </div>
                      </div>
                    </div>
                  </div>
        </div>
        <div className="w-[1680px] h-[350px]" />
      </div>
      <div className="flex flex-col justify-start items-center gap-16 py-24 w-[1680px] border border-border overflow-hidden">
            <p className="text-sm font-normal leading-[20px] text-text-muted">© Saturam Inc 2026. All rights reserved.</p>
      </div>
    </div>
  );
}
