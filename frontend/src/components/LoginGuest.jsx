// LoginGuest - Auto-generated from Figma
// Figma node: 17609:310980
// Screen name: Log in
// Screenshot reference: ../screenshots/LoginPageVariant.png

import React from "react";
import logoSvg from "../assets/retailgpt-logo.svg";

export default function LoginGuest() {
  return (
    <div className="flex justify-start items-center p-[48px] w-full min-h-screen bg-surface">
        <div className="rounded-[31.999996185302734px] w-full h-full overflow-hidden" style={{ backgroundImage: "linear-gradient(135deg, rgba(255_61_0_0.15) 0.0%, rgba(0_33_165_0.10) 87.0%)" }}>
          <div className="w-[1426px] h-[1031px] bg-[rgba(198_209_255_0.65)] rounded" />
          <div className="w-[1173px] h-[924px] bg-[rgba(119_144_241_0.25)] rounded" />
        </div>
      <div className="flex flex-col justify-center items-center w-full h-full overflow-hidden">
          <div className="flex flex-col justify-center items-center gap-6">
              <div className="flex justify-start items-center gap-1">
                  <img src={logoSvg} alt="" className="h-8 w-8 rounded-lg" />
                  <p className="text-xl font-semibold leading-[28px] tracking-[-0.8px] text-[#2a2a37]">EnterpriseGPT</p>
              </div>
              <div className="flex justify-center items-center gap-1.5 py-10 px-16 rounded-lg w-[259px] h-full shadow-sm overflow-hidden bg-[#6840c6]">
                  <p className="text-base font-semibold leading-[24px] text-[#ffffff]">Continue as a guest user</p>
                <div className="w-[24px] h-[24px] overflow-hidden" />
              </div>
          </div>
          <div className="flex justify-start items-center gap-1">
              <img src={logoSvg} alt="" className="h-8 w-8 rounded-lg" />
              <p className="text-xl font-semibold leading-[28px] tracking-[-0.8px] text-[#2a2a37]">EnterpriseGPT</p>
          </div>
      </div>
    </div>
  );
}
