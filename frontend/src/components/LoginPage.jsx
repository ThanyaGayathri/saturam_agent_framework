import React, { useEffect, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { usePrototypeAuth } from "../context/PrototypeAuthContext";
import logoSvg from "../assets/retailgpt-logo.svg";
import avatarLoginLeft from "../assets/avatar_login_left.svg";

function StarIcon() {
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="#F59E0B" stroke="#F59E0B" strokeWidth="1">
      <polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2" />
    </svg>
  );
}

function GoogleIcon() {
  return (
    <svg className="w-6 h-6" viewBox="0 0 24 24" fill="none">
      <path d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92a5.06 5.06 0 0 1-2.2 3.32v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.1z" fill="#4285F4" />
      <path d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z" fill="#34A853" />
      <path d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18A11.96 11.96 0 0 0 1 12c0 1.94.46 3.77 1.18 5.07l3.66-2.98z" fill="#FBBC05" />
      <path d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z" fill="#EA4335" />
    </svg>
  );
}

export default function LoginPage() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [remember, setRemember] = useState(false);
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const navigate = useNavigate();
  const location = useLocation();
  const { ready, authEnabled, isAuthenticated, login, misconfigured } = usePrototypeAuth();

  const from =
    typeof location.state?.from === "string" && location.state.from.startsWith("/")
      ? location.state.from
      : "/playground";

  useEffect(() => {
    if (!ready) return;
    if (!authEnabled) {
      navigate("/playground", { replace: true });
      return;
    }
    if (isAuthenticated) {
      navigate(from, { replace: true });
    }
  }, [ready, authEnabled, isAuthenticated, from, navigate]);

  const handleSubmit = async (e) => {
    e.preventDefault();
    setError("");
    if (!authEnabled) {
      navigate("/playground", { replace: true });
      return;
    }
    setSubmitting(true);
    try {
      const result = await login(email, password, remember);
      if (!result.ok) {
        if (result.error === "server") {
          setError(result.message || "Sign-in is temporarily unavailable.");
        } else {
          setError("Invalid email or password.");
        }
        return;
      }
      navigate(from, { replace: true });
    } finally {
      setSubmitting(false);
    }
  };

  if (!ready) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-surface text-sm text-text-secondary">
        Loading…
      </div>
    );
  }

  if (misconfigured) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-surface p-8">
        <div className="max-w-md rounded-xl border border-amber-200 bg-amber-50 p-6 text-center text-sm text-amber-950">
          <p className="font-semibold">Prototype login is not configured correctly</p>
          <p className="mt-2 text-amber-900/90">
            Set <code className="rounded bg-amber-100/80 px-1">PROTOTYPE_AUTH_SECRET</code> (min 16 characters) and{" "}
            <code className="rounded bg-amber-100/80 px-1">PROTOTYPE_LOGIN_PASSWORD</code> as server environment variables
            on Vercel, then redeploy.
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="flex min-h-screen w-full items-center justify-center bg-surface p-8">
      <div className="flex h-[calc(100vh-64px)] w-full overflow-hidden rounded-[32px] bg-surface">
        {/* Left Panel - Testimonial */}
        <div className="relative flex h-full flex-1 flex-col items-center justify-center overflow-hidden">
        {/* Gradient background */}
        <div
          className="absolute inset-0"
          style={{
            backgroundImage:
              "linear-gradient(135deg, rgba(226,213,255,0.58) 0%, rgba(203,219,255,0.72) 35%, rgba(194,170,255,0.44) 72%, rgba(236,242,255,0.72) 100%)",
          }}
        />

        {/* Decorative circles */}
        <div
          className="pointer-events-none absolute -left-[200px] top-[110px] h-[800px] w-[800px] rounded-full opacity-10"
          style={{ background: "radial-gradient(circle, rgba(92,84,220,0.18) 0%, transparent 70%)" }}
        />
        <div
          className="pointer-events-none absolute -right-[120px] -top-[130px] h-[620px] w-[620px] rounded-full opacity-10"
          style={{ background: "radial-gradient(circle, rgba(105,65,198,0.06) 0%, transparent 70%)" }}
        />

        {/* Logo - top left */}
        <div className="absolute left-8 top-8 flex items-center gap-1">
          <img src={logoSvg} alt="" className="h-8 w-8" />
          <span className="text-xl tracking-[-0.8px] text-[#2a2a37]">
            <span className="font-semibold">Enterprise</span>
            <span className="font-bold">GPT</span>
          </span>
        </div>

        {/* Testimonial content */}
        <div className="relative z-10 flex max-w-[560px] flex-col items-center gap-8 px-8 text-center mt-4">
          <p className="text-[30px] font-medium leading-[38px] text-text-primary">
            EnterpriseGPT helps us run our stores smarter with real-time insights
            and AI-driven decisions.
          </p>

          {/* Avatar + attribution */}
          <div className="flex flex-col items-center gap-4">
            <div className="relative h-16 w-16">
              <div className="flex h-full w-full items-center justify-center overflow-hidden rounded-full border border-[rgba(0,0,0,0.08)] bg-[#ced1e5]">
                <img
                  src={avatarLoginLeft}
                  alt="Sarah Chen"
                  className="h-full w-full object-cover"
                />
              </div>
              <div className="absolute -bottom-[3px] -right-[3px] flex h-5 w-5 items-center justify-center rounded-full border-[1.667px] border-white bg-primary">
                <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="white" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round">
                  <polyline points="20 6 9 17 4 12" />
                </svg>
              </div>
            </div>
            <div className="flex flex-col items-center gap-1">
              <p className="text-base font-semibold leading-6 text-text-primary">
                Sarah Chen
              </p>
              <p className="text-sm font-medium leading-5 text-text-secondary">
                Director of Store Operations, UrbanStyle
              </p>
            </div>
          </div>

          {/* Stars */}
          <div className="flex gap-1">
            {[...Array(5)].map((_, i) => (
              <StarIcon key={i} />
            ))}
          </div>
        </div>

        {/* Footer */}
        <p className="absolute bottom-8 left-8 text-sm text-text-secondary">
          &copy; Saturam Inc 2026
        </p>
        </div>

        {/* Right Panel - Login Form */}
        <div
          className="flex h-full flex-1 flex-col items-center justify-center p-12"
          style={{
            minWidth: 480,
            // Make the right panel distinct and prevent it from visually "merging" with the card/background.
            backgroundImage:
              "linear-gradient(180deg, rgba(255,250,241,0.98) 0%, rgba(255,255,255,0.99) 60%, rgba(255,255,255,1) 100%)",
            backgroundColor: "rgba(255,250,241,0.95)",
          }}
        >
          <div className="flex w-full max-w-[360px] flex-col gap-8">
          {/* Header */}
          <div className="flex flex-col gap-3">
            <h1 className="text-[30px] font-semibold leading-[38px] text-text-primary">
              Welcome back
            </h1>
            <p className="text-base leading-6 text-text-secondary">
              Welcome back! Please enter your details.
            </p>
          </div>

          {/* Form */}
          <form onSubmit={handleSubmit} className="flex flex-col gap-6">
            {error ? (
              <p className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800" role="alert">
                {error}
              </p>
            ) : null}
            <div className="flex flex-col gap-1.5">
              <label htmlFor="email" className="text-sm font-medium leading-5 text-text-tertiary">
                Email or username
              </label>
              <input
                id="email"
                type="text"
                autoComplete="username"
                placeholder="Enter your email or username"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                className="w-full rounded-lg border border-border-light bg-surface px-3.5 py-2.5 text-base leading-6 text-text-primary placeholder-[#717680] outline-none focus:border-primary focus:ring-1 focus:ring-[#6941c6]"
              />
            </div>

            <div className="flex flex-col gap-1.5">
              <label htmlFor="password" className="text-sm font-medium leading-5 text-text-tertiary">
                Password
              </label>
              <input
                id="password"
                type="password"
                autoComplete="current-password"
                placeholder="••••••••"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                className="w-full rounded-lg border border-border-light bg-surface px-3.5 py-2.5 text-base leading-6 text-text-primary placeholder-[#717680] outline-none focus:border-primary focus:ring-1 focus:ring-[#6941c6]"
              />
            </div>

            <div className="flex items-center justify-between">
              <label className="flex cursor-pointer items-center gap-2">
                <input
                  type="checkbox"
                  checked={remember}
                  onChange={(e) => setRemember(e.target.checked)}
                  className="h-4 w-4 rounded border-border-light text-primary focus:ring-[#6941c6]"
                />
                <span className="text-sm font-medium text-text-tertiary">Remember for 30 days</span>
              </label>
              <button type="button" className="text-sm font-semibold text-primary hover:text-[#5b35b3]">
                Forgot password
              </button>
            </div>

            <button
              type="submit"
              disabled={submitting}
              className="w-full rounded-lg bg-primary px-4 py-2.5 text-base font-semibold leading-6 text-white shadow-sm transition-colors hover:bg-primary-hover disabled:opacity-60"
            >
              {submitting ? "Signing in…" : "Sign in"}
            </button>

            <button
              type="button"
              className="flex w-full items-center justify-center gap-3 rounded-lg border border-border-light bg-surface px-4 py-2.5 shadow-sm transition-colors hover:bg-surface-secondary"
            >
              <GoogleIcon />
              <span className="text-base font-semibold leading-6 text-text-tertiary">Sign in with Google</span>
            </button>
          </form>

          <p className="text-center text-sm text-text-secondary">
            Don&apos;t have an account?{" "}
            <button className="font-semibold text-primary hover:text-[#5b35b3]">Sign up</button>
          </p>
          </div>
        </div>
      </div>
    </div>
  );
}
