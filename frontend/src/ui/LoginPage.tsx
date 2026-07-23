import { useEffect, useState } from "react";
import { Github, LogIn } from "lucide-react";
import type { SiteBranding } from "../adk/client";
import { fetchProviders, loginTo, type Provider } from "../adk/identity";
import defaultSiteLogo from "../assets/volcengine.svg";
import { TextShimmer } from "./text-shimmer/TextShimmer";

function providerIcon(id: string) {
  if (id.toLowerCase() === "github") return <Github className="icon" />;
  return <LogIn className="icon" />;
}

export interface LoginPageProps {
  branding: SiteBranding;
}

export function LoginPage({ branding }: LoginPageProps) {
  const [providers, setProviders] = useState<Provider[] | null>(null);
  const [providerError, setProviderError] = useState("");
  const [providerAttempt, setProviderAttempt] = useState(0);

  useEffect(() => {
    let active = true;
    setProviders(null);
    setProviderError("");
    fetchProviders()
      .then((nextProviders) => {
        if (active) setProviders(nextProviders);
      })
      .catch((error) => {
        if (active) {
          setProviderError(error instanceof Error ? error.message : String(error));
        }
      });
    return () => {
      active = false;
    };
  }, [providerAttempt]);

  return (
    <div className="login">
      <header className="login-top">
        <span className="login-brand">
          <img
            className="login-brand-logo"
            src={branding.logoUrl || defaultSiteLogo}
            width={20}
            height={20}
            alt=""
            aria-hidden
          />
          {branding.title}
        </span>
      </header>

      <main className="login-main">
        <div className="login-card">
          <TextShimmer as="h1" className="login-title" duration={4.8} spread={22}>
            {branding.title}
          </TextShimmer>

          {providerError ? (
            <div className="login-provider-error" role="alert">
              <p>{providerError}</p>
              <button type="button" onClick={() => setProviderAttempt((attempt) => attempt + 1)}>
                重试
              </button>
            </div>
          ) : providers === null ? null : (
            <>
              <p className="login-sub">登录以继续使用</p>
              <div className="login-providers">
                {(providers.length > 0
                  ? providers
                  : [
                      {
                        id: "volcengine-identity",
                        label: "火山引擎 Identity",
                        loginUrl: "/oauth2/login",
                      },
                    ]
                ).map((p) => (
                  <button key={p.id} className="login-btn" onClick={() => loginTo(p.loginUrl)}>
                    {providerIcon(p.id)}
                    <span>使用 {p.label} 登录</span>
                  </button>
                ))}
              </div>
            </>
          )}

          <p className="login-powered">火山引擎 AgentKit 提供企业级 Agent 解决方案</p>
          <p className="login-legal">
            继续即表示你已阅读并同意 AgentKit{" "}
            <a
              href="https://docs.volcengine.com/docs/86681/1925174?lang=zh"
              target="_blank"
              rel="noreferrer"
            >
              产品和服务条款
            </a>
          </p>
        </div>
      </main>

      <footer className="login-footer">© 2026 VeADK. All rights reserved.</footer>
    </div>
  );
}
