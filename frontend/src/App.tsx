import { useEffect, useState } from "react";
import { api, auth } from "./api/client";
import type { Me } from "./api/types";
import { Layout } from "./components/Layout";
import { today } from "./lib/format";
import { useRoute } from "./lib/router";
import { DashboardPage } from "./pages/DashboardPage";
import { LoginPage } from "./pages/LoginPage";
import { LoanDetailPage } from "./pages/loans/LoanDetailPage";
import { LoanFormPage } from "./pages/loans/LoanFormPage";
import { LoansPage } from "./pages/loans/LoansPage";
import { SchedulePage } from "./pages/SchedulePage";
import { AuditPage, ReferencePage, UsersPage } from "./pages/admin/AdminPages";
import { EventsPage } from "./pages/events/EventsPage";
import { NoticesPage } from "./pages/NoticesPage";
import { NotificationsPage } from "./pages/NotificationsPage";
import { PaymentsPage } from "./pages/PaymentsPage";

const TABS = ["info", "rates", "schedule", "evidence", "history"] as const;

export default function App() {
  const [signedIn, setSignedIn] = useState(() => Boolean(auth.token));
  const [asOf, setAsOf] = useState(today());
  const [me, setMe] = useState<Me | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const route = useRoute();

  useEffect(() => {
    if (!signedIn) { setMe(null); return; }
    api.me().then(setMe).catch(() => setMe(null));
  }, [signedIn]);

  useEffect(() => {
    const onExpired = () => setSignedIn(false);
    window.addEventListener("auth:expired", onExpired);
    return () => window.removeEventListener("auth:expired", onExpired);
  }, []);

  if (!signedIn) return <LoginPage notice={notice} onDone={() => { setNotice(null); setSignedIn(true); }} />;

  const [section, sub] = route.path;
  let title = "대시보드";
  let page = <DashboardPage asOf={asOf} canDownload={me?.can_download ?? false} />;
  if (section === "loans") {
    title = "차입금 관리";
    if (sub === "new") page = <LoanFormPage me={me} defaultDormitory={route.query.get("dormitory") ?? undefined} />;
    else if (sub) {
      const t = route.query.get("tab") as (typeof TABS)[number] | null;
      page = <LoanDetailPage id={sub} tab={t && TABS.includes(t) ? t : "info"} me={me} asOf={asOf} />;
    } else page = <LoansPage me={me} asOf={asOf} />;
  } else if (section === "schedule") {
    title = "원리금 테이블";
    page = <SchedulePage me={me} asOf={asOf} loanId={route.query.get("loan") ?? undefined} />;
  } else if (section === "events") {
    title = "이벤트 관리";
    const t = route.query.get("tab");
    page = <EventsPage me={me} asOf={asOf} loanId={route.query.get("loan") ?? undefined}
      tab={t === "list" || t === "approval" ? t : "new"} />;
  } else if (section === "payments") {
    title = "실제 상환관리";
    page = <PaymentsPage me={me} asOf={asOf} tab={route.query.get("tab") === "loan" ? "loan" : "overdue"} loanId={route.query.get("loan") ?? undefined} />;
  } else if (section === "notices") {
    title = "안내공문";
    page = <NoticesPage me={me} asOf={asOf} tab={route.query.get("tab") === "templates" ? "templates" : "issue"} dormId={route.query.get("dormitory") ?? undefined} />;
  } else if (section === "notifications") {
    title = "알림";
    page = <NotificationsPage me={me} asOf={asOf} />;
  } else if (section === "admin") {
    const tab = route.query.get("tab") ?? "";
    if (sub === "reference") { title = "기준정보"; page = <ReferencePage me={me} tab={tab} />; }
    else if (sub === "users") { title = "사용자·권한"; page = <UsersPage me={me} />; }
    else { title = "감사로그"; page = <AuditPage me={me} tab={tab} />; }
  }
  const navKey = section === "admin" ? `admin/${sub ?? "audit"}` : section ?? "dashboard";

  return (
    <Layout asOf={asOf} onAsOf={setAsOf} me={me} section={navKey} title={title}
      onLogout={() => { auth.clear(); setSignedIn(false); }}
      onPasswordChanged={() => { auth.clear(); setNotice("비밀번호를 변경했습니다. 새 비밀번호로 다시 로그인하세요."); setSignedIn(false); }}>
      {page}
    </Layout>
  );
}
