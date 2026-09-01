// Vendored from frontend-comps (src/components/authentication/AuthProvider.jsx).
import { createContext, useContext } from "react";
import { useMsal, useIsAuthenticated } from "@azure/msal-react";
import { defaultLoginRequest } from "./msalConfig.js";

const AuthContext = createContext(null);

export const useAuth = () => {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error("useAuth must be used within AuthProvider");
  }
  return context;
};

/**
 * Provides authentication state and helpers to the component tree.
 *
 * @param {Object} props
 * @param {React.ReactNode} props.children
 * @param {{ scopes: string[] }} [props.loginRequest] - Custom login request (defaults to defaultLoginRequest).
 * @param {string} [props.postLogoutRedirectUri] - Where to redirect after logout (defaults to "/login").
 */
export const AuthProvider = ({
  children,
  loginRequest = defaultLoginRequest,
  postLogoutRedirectUri = "/login",
}) => {
  const { instance, accounts, inProgress } = useMsal();
  const isAuthenticated = useIsAuthenticated();

  const signInWithMicrosoft = async () => {
    try {
      await instance.loginRedirect(loginRequest);
    } catch (error) {
      console.error("Login failed:", error);
      throw error;
    }
  };

  const signOut = async () => {
    try {
      await instance.logoutRedirect({
        postLogoutRedirectUri,
      });
    } catch (error) {
      console.error("Logout failed:", error);
      throw error;
    }
  };

  const value = {
    user: accounts[0] || null,
    isAuthenticated,
    loading: inProgress === "login" || inProgress === "sso",
    signInWithMicrosoft,
    signOut,
  };

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
};
