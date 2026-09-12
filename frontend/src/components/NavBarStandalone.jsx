// NavBarStandalone - Auto-generated from Figma
// Figma node: 18723:5336
// Screen name: Menu
// Screenshot reference: ../screenshots/Menu.png

import React from "react";
import logoSvg from "../assets/retailgpt-logo.svg";

export default function NavBarStandalone() {
  return (
    <div className="flex flex-col justify-start items-start p-24 rounded-3xl w-full min-h-screen shadow-[0px_2px_6px_rgba(10_12_18_0.05)] bg-surface">
      <div className="flex flex-col justify-start items-center overflow-hidden">
        <div className="flex justify-start items-center gap-[50.0px] px-[32px] w-full h-[72px]">
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
    </div>
  );
}
