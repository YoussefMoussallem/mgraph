// Vendored from frontend-comps (src/components/authentication/LoginPage.jsx).
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useIsAuthenticated, useMsal } from "@azure/msal-react";
import { defaultLoginRequest } from "./msalConfig.js";
import defaultLogo from "../assets/SLogoLong.png";

const DefaultMicrosoftIcon = () => (
  <svg width="22" height="22" fill="currentColor" viewBox="0 0 24 24">
    <path d="M11.4 24H0V12.6h11.4V24zM24 24H12.6V12.6H24V24zM11.4 11.4H0V0h11.4v11.4zm12.6 0H12.6V0H24v11.4z" />
  </svg>
);

/**
 * Branded Microsoft sign-in page backed by MSAL. Redirects when already authenticated.
 *
 * @param {Object} props
 * @param {string} props.logoSrc - Required logo image URL.
 * @param {string} [props.logoAlt]
 * @param {number} [props.logoHeight]
 * @param {string} [props.productName] - Bold accent-colored text in the heading.
 * @param {string} [props.headingPrefix] - Text before productName (defaults to "Welcome to ").
 * @param {string} [props.tagline] - Subtitle below the heading.
 * @param {string} [props.buttonLabel] - Sign-in button label.
 * @param {string} [props.signingInLabel] - Button label while redirect is in progress.
 * @param {string} [props.dividerText]
 * @param {string} [props.footerText]
 * @param {string} [props.accentColor] - Primary accent (hex), used for productName + button base.
 * @param {string} [props.accentColorLight] - Lighter accent endpoint for button gradient.
 * @param {string} [props.backgroundGradient] - Full CSS gradient for page background.
 * @param {React.ReactNode} [props.buttonIcon] - Icon shown in the sign-in button.
 * @param {{ scopes: string[] }} [props.loginRequest] - MSAL login request (defaults to defaultLoginRequest).
 * @param {string} [props.postLoginRedirect] - Path to navigate to after authentication (defaults to "/").
 * @param {number} [props.fadeDurationMs] - Fade-out duration before redirect (defaults to 600).
 */
export default function LoginPage({
  logoSrc = defaultLogo,
  logoAlt = "Strategy& — Part of the PwC network",
  logoHeight = 64,
  productName,
  headingPrefix = "Welcome to ",
  tagline,
  buttonLabel = "Sign in with Microsoft",
  signingInLabel = "Signing in...",
  dividerText = "Secured by Microsoft Azure AD",
  footerText = "Part of the PwC network",
  accentColor = "#8E1E1E",
  accentColorLight = "#a82828",
  backgroundGradient = "linear-gradient(160deg, #fff 0%, #fdf6f6 50%, #f9eded 100%)",
  buttonIcon = <DefaultMicrosoftIcon />,
  loginRequest = defaultLoginRequest,
  postLoginRedirect = "/",
  fadeDurationMs = 600,
}) {
  const { instance, inProgress } = useMsal();
  const isAuthenticated = useIsAuthenticated();
  const navigate = useNavigate();
  const [fadeOut, setFadeOut] = useState(false);

  useEffect(() => {
    if (isAuthenticated) {
      setFadeOut(true);
      const timer = setTimeout(
        () => navigate(postLoginRedirect, { replace: true }),
        fadeDurationMs,
      );
      return () => clearTimeout(timer);
    }
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = "auto";
    };
  }, [isAuthenticated, navigate, postLoginRedirect, fadeDurationMs]);

  const isProcessing = inProgress !== "none";

  return (
    <div
      style={{
        position: "fixed",
        inset: 0,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        background: backgroundGradient,
        overflow: "hidden",
        opacity: fadeOut ? 0 : 1,
        transform: fadeOut ? "scale(1.04)" : "scale(1)",
        transition:
          "opacity 0.55s cubic-bezier(0.4,0,0.2,1), transform 0.55s cubic-bezier(0.4,0,0.2,1)",
      }}
    >
      <style>{`
        .lp-card {
          max-width: 480px;
          width: 100%;
          padding: 0 2rem;
          animation: lp-enter 0.7s cubic-bezier(0.16, 1, 0.3, 1) both;
        }
        @keyframes lp-enter {
          from { opacity: 0; transform: translateY(32px) scale(0.97); }
          to   { opacity: 1; transform: translateY(0) scale(1); }
        }
        .lp-btn {
          width: 100%;
          padding: 1.1rem 1.5rem;
          color: white;
          font-weight: 600;
          font-size: 1.05rem;
          border-radius: 10px;
          display: flex;
          align-items: center;
          justify-content: center;
          gap: 0.75rem;
          border: none;
          cursor: pointer;
          background: linear-gradient(135deg, ${accentColor} 0%, ${accentColorLight} 100%);
          box-shadow: 0 4px 16px ${accentColor}40, 0 1px 3px ${accentColor}26;
          transition: all 0.25s cubic-bezier(0.4,0,0.2,1);
          position: relative;
          overflow: hidden;
        }
        .lp-btn::after {
          content: '';
          position: absolute;
          inset: 0;
          background: linear-gradient(135deg, transparent 0%, rgba(255,255,255,0.08) 100%);
          opacity: 0;
          transition: opacity 0.25s;
        }
        .lp-btn:hover:not(:disabled)::after { opacity: 1; }
        .lp-btn:hover:not(:disabled) {
          transform: translateY(-1px);
          box-shadow: 0 6px 24px ${accentColor}59, 0 2px 6px ${accentColor}33;
        }
        .lp-btn:active:not(:disabled) {
          transform: translateY(0) scale(0.985);
          box-shadow: 0 2px 8px ${accentColor}33;
        }
        .lp-btn:disabled {
          cursor: wait;
          opacity: 0.85;
        }
        .lp-divider {
          position: relative;
          text-align: center;
          margin: 2.5rem 0;
        }
        .lp-divider::before {
          content: '';
          position: absolute;
          left: 10%;
          right: 10%;
          top: 50%;
          height: 1px;
          background: linear-gradient(90deg, transparent, #e0d0d0, transparent);
        }
        .lp-divider span {
          position: relative;
          background: ${backgroundGradient};
          padding: 0 1.25rem;
          color: #9a8888;
          font-size: 0.82rem;
          letter-spacing: 0.03em;
        }
        @keyframes lp-spin { to { transform: rotate(360deg); } }
        .lp-spinner {
          width: 18px; height: 18px;
          border: 2px solid rgba(255,255,255,0.25);
          border-top-color: #fff;
          border-radius: 50%;
          animation: lp-spin 0.65s linear infinite;
        }
      `}</style>

      <div className="lp-card">
        {logoSrc && (
          <div style={{ textAlign: "center", marginBottom: "3rem" }}>
            <img
              src={logoSrc}
              alt={logoAlt}
              style={{ height: logoHeight, margin: "0 auto" }}
            />
          </div>
        )}

        {(productName || tagline) && (
          <div style={{ textAlign: "center", marginBottom: "2.5rem" }}>
            {productName && (
              <h1
                style={{
                  fontSize: "1.75rem",
                  fontWeight: 300,
                  color: "#1a1a1a",
                  margin: "0 0 0.5rem",
                  letterSpacing: "-0.01em",
                }}
              >
                {headingPrefix}
                <span style={{ fontWeight: 600, color: accentColor }}>{productName}</span>
              </h1>
            )}
            {tagline && (
              <p style={{ color: "#888", fontSize: "0.95rem", margin: 0, lineHeight: 1.5 }}>
                {tagline}
              </p>
            )}
          </div>
        )}

        <button
          onClick={() => instance.loginRedirect(loginRequest)}
          className="lp-btn"
          disabled={isProcessing}
        >
          {isProcessing ? (
            <>
              <div className="lp-spinner" />
              {signingInLabel}
            </>
          ) : (
            <>
              {buttonIcon}
              {buttonLabel}
            </>
          )}
        </button>

        {dividerText && (
          <div className="lp-divider">
            <span>{dividerText}</span>
          </div>
        )}

        {footerText && (
          <p
            style={{
              fontSize: "0.78rem",
              color: "#b0a0a0",
              textAlign: "center",
              margin: 0,
              letterSpacing: "0.04em",
            }}
          >
            {footerText}
          </p>
        )}
      </div>
    </div>
  );
}
