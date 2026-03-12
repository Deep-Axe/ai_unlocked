import { useState, useEffect, useCallback, useRef } from "react";
import axios from "axios";
import {
  LineChart, Line, BarChart, Bar, XAxis, YAxis, CartesianGrid,
  Tooltip, Legend, ResponsiveContainer, PieChart, Pie, Cell,
  ReferenceLine,
} from "recharts";

const API_BASE  = "";
const TENANT_ID = 1;

// ── Module-level formatters (used by components outside App()) ────────────
const formatINR  = (v) => `₹${Number(v || 0).toLocaleString("en-IN")}`;
const formatDate = (iso) =>
  new Date(iso).toLocaleDateString("en-IN", { day: "numeric", month: "short" });

// ── Persona config ────────────────────────────────────────────────────────
const PERSONAS = {
  rohan:    { label: "Rohan",    role: "Procurement Manager", initial: "R", color: "bg-blue-600"    },
  harpreet: { label: "Harpreet", role: "Owner",               initial: "H", color: "bg-violet-600"  },
  meena:    { label: "Meena",    role: "Supplier",             initial: "M", color: "bg-emerald-600" },
};

const PERSONA_TABS = {
  rohan:    ["Alerts", "Recommendations", "Vendors", "Procurement"],
  harpreet: ["Overview", "Spend", "Vendor Health"],
  meena:    ["My Performance", "RFQ Inbox", "Open Orders"],
};

const VENDOR_COLORS = {
  "Punjab Components House": "#3b82f6",
  "Sharma Textiles":         "#f59e0b",
  "Gupta Packaging Co":      "#ef4444",
};

const MONTH_NAMES = ["","Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];

// ── Logo mark ─────────────────────────────────────────────────────────────
function LogoMark() {
  return (
    <div className="w-6 h-6 rounded-md bg-blue-600 flex items-center justify-center flex-shrink-0">
      <svg width="13" height="13" viewBox="0 0 13 13" fill="none">
        <rect x="0"   y="0"   width="5.5" height="5.5" rx="1.2" fill="white" fillOpacity="0.95"/>
        <rect x="7.5" y="0"   width="5.5" height="5.5" rx="1.2" fill="white" fillOpacity="0.45"/>
        <rect x="0"   y="7.5" width="5.5" height="5.5" rx="1.2" fill="white" fillOpacity="0.45"/>
        <rect x="7.5" y="7.5" width="5.5" height="5.5" rx="1.2" fill="white" fillOpacity="0.95"/>
      </svg>
    </div>
  );
}

// ── Toggle ────────────────────────────────────────────────────────────────
function Toggle({ checked, onChange, danger = false }) {
  return (
    <div
      onClick={onChange}
      className={`w-9 h-5 rounded-full transition-colors cursor-pointer relative flex-shrink-0 ${
        checked ? (danger ? "bg-red-500" : "bg-slate-900") : "bg-slate-200"
      }`}
    >
      <div
        className="w-3.5 h-3.5 bg-white rounded-full absolute shadow transition-all"
        style={{ top: "3px", left: checked ? "19px" : "3px" }}
      />
    </div>
  );
}

// ── Status badge styles ───────────────────────────────────────────────────
const statusStyles = {
  critical:  "bg-red-50 text-red-700 ring-1 ring-inset ring-red-200",
  low:       "bg-amber-50 text-amber-700 ring-1 ring-inset ring-amber-200",
  ok:        "bg-emerald-50 text-emerald-700 ring-1 ring-inset ring-emerald-200",
  pending:   "bg-amber-50 text-amber-700 ring-1 ring-inset ring-amber-200",
  approved:  "bg-emerald-50 text-emerald-700 ring-1 ring-inset ring-emerald-200",
  rejected:  "bg-red-50 text-red-700 ring-1 ring-inset ring-red-200",
  no_vendor: "bg-orange-50 text-orange-700 ring-1 ring-inset ring-orange-200",
};

const barColor   = (s) => s === "critical" ? "bg-red-500" : s === "low" ? "bg-amber-400" : "bg-emerald-500";
const scoreColor = (s) => s >= 80 ? "text-emerald-600" : s >= 50 ? "text-amber-600" : "text-red-600";
const scoreBar   = (s) => s >= 80 ? "bg-emerald-500"   : s >= 50 ? "bg-amber-400"   : "bg-red-500";

// ── Spinner ───────────────────────────────────────────────────────────────
function Spinner({ className = "" }) {
  return (
    <span className={`inline-block rounded-full border-2 border-t-transparent animate-spin ${className}`} />
  );
}

// ── Skeleton loaders ──────────────────────────────────────────────────────
function Skeletons() {
  return (
    <div className="animate-pulse space-y-3">
      {[1, 2, 3].map((i) => (
        <div key={i} className="bg-slate-100 rounded-xl h-28 border border-slate-200" />
      ))}
    </div>
  );
}

function ChartSkeleton({ height = 240 }) {
  return (
    <div
      className="animate-pulse bg-slate-100 rounded-xl border border-slate-200"
      style={{ height }}
    />
  );
}

// ── KPI card ──────────────────────────────────────────────────────────────
function KpiCard({ label, value, sub, accent = "blue" }) {
  const accents = {
    blue:   "border-blue-200 bg-blue-50",
    red:    "border-red-200 bg-red-50",
    amber:  "border-amber-200 bg-amber-50",
    green:  "border-emerald-200 bg-emerald-50",
    violet: "border-violet-200 bg-violet-50",
  };
  return (
    <div className={`rounded-xl border p-4 ${accents[accent]}`}>
      <p className="text-xs text-slate-500 mb-1">{label}</p>
      <p className="text-xl font-bold text-slate-900 leading-none">{value}</p>
      {sub && <p className="text-xs text-slate-400 mt-1">{sub}</p>}
    </div>
  );
}

// ── Section header ────────────────────────────────────────────────────────
function SectionHeader({ title, sub }) {
  return (
    <div className="mb-5">
      <h2 className="text-slate-900 font-semibold text-sm">{title}</h2>
      {sub && <p className="text-slate-500 text-xs mt-0.5">{sub}</p>}
    </div>
  );
}

// ── Delivery dot ──────────────────────────────────────────────────────────
function DeliveryDot({ was_on_time, had_quality_issue }) {
  const color = had_quality_issue
    ? "bg-red-400"
    : was_on_time
    ? "bg-emerald-400"
    : "bg-amber-400";
  return (
    <span
      className={`inline-block w-2.5 h-2.5 rounded-full ${color}`}
      title={had_quality_issue ? "Quality issue" : was_on_time ? "On time" : "Late"}
    />
  );
}

// ── Score explainer modal ─────────────────────────────────────────────────
function ScoreExplainer({ vendor, onClose }) {
  if (!vendor) return null;
  const onTimeRate  = vendor.total_orders > 0 ? vendor.on_time_rate / 100 : 0;
  const qualityRate = vendor.total_orders > 0 ? vendor.quality_issues / vendor.total_orders : 0;
  const onTimePart  = (onTimeRate * 70).toFixed(1);
  const qualityPart = ((1 - qualityRate) * 30).toFixed(1);

  return (
    <div className="fixed inset-0 bg-black/40 z-50 flex items-center justify-center p-4" onClick={onClose}>
      <div className="bg-white rounded-2xl p-6 max-w-sm w-full shadow-xl" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between mb-4">
          <h3 className="font-semibold text-slate-900 text-sm">Score Breakdown</h3>
          <button onClick={onClose} className="text-slate-400 hover:text-slate-600 text-xs">✕</button>
        </div>
        <p className="text-2xl font-bold text-slate-900 mb-1">
          {vendor.score.toFixed(1)}<span className="text-slate-400 text-sm font-normal">/100</span>
        </p>
        <p className="text-xs text-slate-500 mb-4">{vendor.vendor_name}</p>
        <div className="space-y-2 text-xs">
          <div className="flex justify-between bg-slate-50 rounded-lg px-3 py-2">
            <span className="text-slate-600">Total orders</span>
            <span className="font-semibold">{vendor.total_orders}</span>
          </div>
          <div className="flex justify-between bg-emerald-50 rounded-lg px-3 py-2">
            <span className="text-slate-600">On-time rate ({vendor.on_time_rate}%) × 70</span>
            <span className="font-semibold text-emerald-700">+{onTimePart}</span>
          </div>
          <div className="flex justify-between bg-blue-50 rounded-lg px-3 py-2">
            <span className="text-slate-600">Quality ({vendor.quality_issues} issues) × 30</span>
            <span className="font-semibold text-blue-700">+{qualityPart}</span>
          </div>
          <div className="flex justify-between bg-slate-900 rounded-lg px-3 py-2">
            <span className="text-white font-semibold">Final score</span>
            <span className="text-white font-bold">{vendor.score.toFixed(1)}</span>
          </div>
        </div>
        <p className="text-[11px] text-slate-400 mt-3 text-center">
          On-time delivery weighted 70% · Quality 30%
        </p>
      </div>
    </div>
  );
}

// ── Vendor health card (fetches its own delivery history) ─────────────────
function VendorHealthCard({ vendor, onScoreClick, refreshTrigger = 0 }) {
  const [history, setHistory] = useState(null);

  useEffect(() => {
    axios
      .get(`/vendors/${vendor.vendor_id}/delivery-history?tenant_id=${TENANT_ID}`)
      .then((res) => setHistory(res.data))
      .catch(() => setHistory([]));
  }, [vendor.vendor_id, refreshTrigger]);

  return (
    <div className="bg-white rounded-xl border border-slate-200 p-5">
      <div className="flex items-start justify-between mb-3">
        <div>
          <p className="font-semibold text-slate-900 text-sm">{vendor.vendor_name}</p>
          <p className="text-slate-400 text-xs mt-0.5">
            {vendor.city} · Lead time {vendor.avg_lead_time}d
          </p>
        </div>
        <button
          onClick={onScoreClick}
          className={`text-xl font-bold ${scoreColor(vendor.score)} hover:underline cursor-pointer`}
          title="Click to see score breakdown"
        >
          {vendor.score.toFixed(1)}
        </button>
      </div>

      <div className="flex items-center justify-between text-xs text-slate-500 mb-3">
        <span>Orders <span className="font-semibold text-slate-700">{vendor.total_orders}</span></span>
        <span>On-time <span className="font-semibold text-slate-700">{vendor.on_time_rate}%</span></span>
        <span>Quality issues <span className="font-semibold text-slate-700">{vendor.quality_issues}</span></span>
        <span>Spend <span className="font-semibold text-slate-700">{formatINR(vendor.total_spend)}</span></span>
      </div>

      <div className="flex gap-1.5 flex-wrap">
        {history === null ? (
          <span className="text-[11px] text-slate-400">Loading history…</span>
        ) : history.length === 0 ? (
          <span className="text-[11px] text-slate-400">No delivery history</span>
        ) : (
          history
            .slice(0, 10)
            .map((d, i) => (
              <DeliveryDot key={i} was_on_time={d.was_on_time} had_quality_issue={d.had_quality_issue} />
            ))
        )}
      </div>
    </div>
  );
}

// ── Harpreet Vendor Health tab (needs its own useState for modal) ──────────
function HarpreetVendorHealth({ vendorComparison, refreshTrigger = 0 }) {
  const [scoreModal, setScoreModal] = useState(null);

  const chartData = vendorComparison
    ? vendorComparison.map((v) => ({
        name:        v.vendor_name.split(" ")[0],
        Score:       v.score,
        "On-time %": v.on_time_rate,
      }))
    : [];

  return (
    <div>
      <SectionHeader title="Vendor Health" sub="Performance scores and delivery history" />

      <div className="bg-white rounded-xl border border-slate-200 p-5 mb-4">
        <p className="text-xs font-semibold text-slate-700 mb-4">Score vs On-Time Rate</p>
        {!vendorComparison ? (
          <ChartSkeleton />
        ) : (
          <ResponsiveContainer width="100%" height={220}>
            <BarChart data={chartData} margin={{ top: 5, right: 10, bottom: 5, left: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#f1f5f9" />
              <XAxis dataKey="name" tick={{ fontSize: 11, fill: "#94a3b8" }} />
              <YAxis domain={[0, 100]} tick={{ fontSize: 11, fill: "#94a3b8" }} />
              <Tooltip contentStyle={{ fontSize: 11, borderRadius: 8 }} />
              <Legend wrapperStyle={{ fontSize: 11 }} />
              <Bar dataKey="Score"      fill="#3b82f6" radius={[4, 4, 0, 0]} />
              <Bar dataKey="On-time %"  fill="#10b981" radius={[4, 4, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        )}
      </div>

      {!vendorComparison ? (
        <div className="space-y-3">
          <ChartSkeleton height={100} />
          <ChartSkeleton height={100} />
        </div>
      ) : (
        <div className="space-y-3">
          {vendorComparison.map((v) => (
            <VendorHealthCard
              key={v.vendor_id}
              vendor={v}
              onScoreClick={() => setScoreModal(v)}
              refreshTrigger={refreshTrigger}
            />
          ))}
        </div>
      )}

      {scoreModal && (
        <ScoreExplainer vendor={scoreModal} onClose={() => setScoreModal(null)} />
      )}
    </div>
  );
}

// ════════════════════════════════════════════════════════════════════════════
export default function App() {

  // ── Persona + tab ────────────────────────────────────────────────────────
  const [persona,   setPersona]   = useState("rohan");
  const [activeTab, setActiveTab] = useState("Alerts");

  // ── Rohan state ──────────────────────────────────────────────────────────
  const [alerts,          setAlerts]          = useState([]);
  const [recommendations, setRecommendations] = useState([]);
  const [vendors,         setVendors]         = useState([]);
  const [loading,         setLoading]         = useState(true);
  const [watchdogLoading, setWatchdogLoading] = useState(false);
  const [toast,           setToast]           = useState(null);
  const [deliveryModal,   setDeliveryModal]   = useState(null);
  const [deliveryForm,    setDeliveryForm]    = useState({ was_on_time: true, had_quality_issue: false });
  const [streamText,      setStreamText]      = useState("");
  const [streamDrawerOpen,setStreamDrawerOpen]= useState(false);
  const [streamingDone,   setStreamingDone]   = useState(false);

  // ── Harpreet / analytics state ───────────────────────────────────────────
  const [spendData,        setSpendData]        = useState(null);
  const [vendorComparison, setVendorComparison] = useState(null);
  const [recHistory,       setRecHistory]       = useState([]);

  // ── Meena state ──────────────────────────────────────────────────────────
  const [deliveryHistory, setDeliveryHistory] = useState(null);
  const [meenaVendorMeta, setMeenaVendorMeta] = useState(null); // vendor row from /vendors
  const [deliveryRefreshKey, setDeliveryRefreshKey] = useState(0); // bumped on each delivery → triggers VendorHealthCard re-fetch
  const meenaHistoryFetched = useRef(false); // prevent vendorComparison re-fetches from overwriting optimistic history

  // ── Procurement pipeline state ────────────────────────────────────────────
  const [orders,                setOrders]                = useState([]);
  const [meenaQuoteForms,       setMeenaQuoteForms]       = useState({});
  const [rfqInbox,              setRfqInbox]              = useState([]);
  const [pendingSpendApprovals, setPendingSpendApprovals] = useState([]);
  const [spendRejectForms,      setSpendRejectForms]      = useState({});
  const [recVendorPicks,        setRecVendorPicks]        = useState({});
  // supplier-app approval forms: { appId: { quotedPrice, leadTime, submitting } }
  const [supplierAppForms,      setSupplierAppForms]      = useState({});

  // ── System health ─────────────────────────────────────────────────────────
  const [systemHealth, setSystemHealth] = useState(null); // null | "healthy" | "degraded"

  // ── Copilot chat ──────────────────────────────────────────────────────────
  const [chatQuestion, setChatQuestion] = useState("");
  const [chatAnswer,   setChatAnswer]   = useState(null);
  const [chatLoading,  setChatLoading]  = useState(false);

  // ── Toast ─────────────────────────────────────────────────────────────────
  const showToast = (message, type = "success") => {
    setToast({ message, type });
    setTimeout(() => setToast(null), 3000);
  };

  // ── Fetchers ──────────────────────────────────────────────────────────────
  const fetchAlerts = useCallback(async () => {
    const res = await axios.get(`${API_BASE}/inventory/alerts?tenant_id=${TENANT_ID}`);
    setAlerts(res.data);
  }, []);

  const fetchRecommendations = useCallback(async () => {
    const res = await axios.get(`${API_BASE}/agents/recommendations?tenant_id=${TENANT_ID}`);
    setRecommendations(res.data);
  }, []);

  const fetchVendors = useCallback(async () => {
    const res = await axios.get(`${API_BASE}/vendors?tenant_id=${TENANT_ID}`);
    setVendors(res.data);
  }, []);

  const fetchSpendData = useCallback(async () => {
    const res = await axios.get(`${API_BASE}/analytics/spend?tenant_id=${TENANT_ID}`);
    setSpendData(res.data.monthly);
  }, []);

  const fetchVendorComparison = useCallback(async () => {
    const res = await axios.get(`${API_BASE}/analytics/vendor-comparison?tenant_id=${TENANT_ID}`);
    setVendorComparison(res.data);
  }, []);

  const fetchRecHistory = useCallback(async () => {
    const res = await axios.get(`${API_BASE}/agents/recommendations/history?tenant_id=${TENANT_ID}`);
    setRecHistory(res.data);
  }, []);

  const fetchOrders = useCallback(async () => {
    const res = await axios.get(`${API_BASE}/agents/orders?tenant_id=${TENANT_ID}`);
    setOrders(res.data);
  }, []);

  const fetchRfqInbox = useCallback(async () => {
    const res = await axios.get(`${API_BASE}/agents/rfq-inbox?tenant_id=${TENANT_ID}`);
    setRfqInbox(res.data);
  }, []);

  const fetchPendingSpendApprovals = useCallback(async () => {
    const res = await axios.get(`${API_BASE}/agents/pending-spend-approvals?tenant_id=${TENANT_ID}`);
    setPendingSpendApprovals(res.data);
  }, []);

  // ── Initial load — all fire independently, none blocks another ───────────
  useEffect(() => {
    fetchAlerts().catch(() => showToast("Failed to load alerts", "error"));
    fetchRecommendations().catch(() => {});
    fetchVendors()
      .catch(() => showToast("Failed to load vendors", "error"))
      .finally(() => setLoading(false));
    // Analytics — loads in background, charts render when ready
    fetchSpendData().catch(() => {});
    fetchVendorComparison().catch(() => {});
    fetchRecHistory().catch(() => {});
    fetchOrders().catch(() => {});
    fetchRfqInbox().catch(() => {});
    fetchPendingSpendApprovals().catch(() => {});
  }, []);

  // ── Health polling — every 30s ────────────────────────────────────────────
  useEffect(() => {
    const poll = () =>
      axios.get(`${API_BASE}/health`).then((res) => setSystemHealth(res.data.status)).catch(() => setSystemHealth("degraded"));
    poll();
    const id = setInterval(poll, 30000);
    return () => clearInterval(id);
  }, []);

  // ── Procurement tab auto-refresh — every 10s when active ─────────────────
  useEffect(() => {
    if (persona !== "rohan" || activeTab !== "Procurement") return;
    const id = setInterval(() => fetchOrders().catch(() => {}), 10000);
    return () => clearInterval(id);
  }, [persona, activeTab, fetchOrders]);

  // ── Meena data — resolves after vendorComparison arrives ─────────────────
  useEffect(() => {
    if (!vendorComparison) return;
    const punjab = vendorComparison.find((v) => v.vendor_name === "Punjab Components House");
    if (!punjab) return;

    // Optimistic: meta from comparison data (available immediately)
    setMeenaVendorMeta({
      id:                punjab.vendor_id,
      name:              punjab.vendor_name,
      score:             punjab.score,
      total_orders:      punjab.total_orders,
      on_time_deliveries:Math.round((punjab.on_time_rate / 100) * punjab.total_orders),
      quality_issues:    punjab.quality_issues,
      lead_time_days:    punjab.avg_lead_time,
      on_time_rate:      punjab.on_time_rate,
    });

    // Delivery history for Meena's performance chart — only on first load
    // (subsequent updates come from submitDelivery's optimistic prepend)
    if (!meenaHistoryFetched.current) {
      meenaHistoryFetched.current = true;
      axios
        .get(`${API_BASE}/vendors/${punjab.vendor_id}/delivery-history?tenant_id=${TENANT_ID}`)
        .then((res) => setDeliveryHistory(res.data))
        .catch(() => setDeliveryHistory([]));
    }
  }, [vendorComparison]);

  // ── Persona switch ────────────────────────────────────────────────────────
  const switchPersona = (key) => {
    setPersona(key);
    setActiveTab(PERSONA_TABS[key][0]);
  };

  // ── Rohan actions ─────────────────────────────────────────────────────────
  const runWatchdog = async () => {
    setWatchdogLoading(true);
    setStreamText("");
    setStreamingDone(false);
    setStreamDrawerOpen(true);
    setActiveTab("Recommendations");

    try {
      const evtSource = new EventSource(
        `${API_BASE}/agents/run-watchdog-stream?tenant_id=${TENANT_ID}`
      );

      await new Promise((resolve, reject) => {
        evtSource.onmessage = (e) => {
          const msg = JSON.parse(e.data);
          if (msg.type === "token") {
            setStreamText((prev) => prev + msg.text);
          } else if (msg.type === "status") {
            setStreamText((prev) => prev + `\n[${msg.message}]\n`);
          } else if (msg.type === "done") {
            evtSource.close();
            setStreamingDone(true);
            const n = msg.recommendations.length;
            showToast(`${n} recommendation${n !== 1 ? "s" : ""} created`);
            fetchRecommendations();
            fetchRecHistory(); // keep Harpreet's history in sync
            resolve();
          } else if (msg.type === "error") {
            evtSource.close();
            reject(new Error(msg.message));
          }
        };
        evtSource.onerror = () => {
          evtSource.close();
          reject(new Error("Stream connection failed"));
        };
      });
    } catch {
      showToast("Watchdog failed — check API server", "error");
    } finally {
      setWatchdogLoading(false);
    }
  };

  const handleApprove = (id) => {
    const chosenVendorId = recVendorPicks[id];
    setRecommendations((prev) => prev.filter((r) => r.id !== id));
    showToast("Placing order — sending RFQ to vendors...");
    const approveUrl = `${API_BASE}/agents/recommendations/${id}/approve?tenant_id=${TENANT_ID}`
      + (chosenVendorId ? `&vendor_id=${chosenVendorId}` : "");
    axios
      .post(approveUrl)
      .then(() => axios.post(`${API_BASE}/agents/recommendations/${id}/send-rfq?tenant_id=${TENANT_ID}`))
      .then(() => {
        fetchAlerts();
        fetchRecHistory();
        fetchOrders();
        showToast("Order placed — RFQ sent to vendors");
      })
      .catch(() => {
        showToast("Failed — please retry", "error");
        fetchRecommendations();
      });
  };

  const handleReject = (id) => {
    setRecommendations((prev) => prev.filter((r) => r.id !== id));
    showToast("Recommendation rejected");
    axios
      .post(`${API_BASE}/agents/recommendations/${id}/reject?tenant_id=${TENANT_ID}`)
      .catch(() => {
        showToast("Rejection failed — please retry", "error");
        fetchRecommendations();
      });
  };

  const handleApproveSpend = (id) => {
    setPendingSpendApprovals((prev) => prev.filter((r) => r.recommendation_id !== id));
    showToast("Spend approved — issuing PO...");
    axios
      .post(`${API_BASE}/agents/recommendations/${id}/approve-spend?tenant_id=${TENANT_ID}`)
      .then(() => {
        fetchPendingSpendApprovals();
        fetchOrders();
        showToast("PO issued after spend approval");
      })
      .catch(() => {
        showToast("Approval failed — please retry", "error");
        fetchPendingSpendApprovals();
      });
  };

  const handleRejectSpend = (id, reason = "") => {
    setPendingSpendApprovals((prev) => prev.filter((r) => r.recommendation_id !== id));
    showToast("Spend rejected");
    axios
      .post(
        `${API_BASE}/agents/recommendations/${id}/reject-spend?tenant_id=${TENANT_ID}&approver=rohan&reason=${encodeURIComponent(reason)}`
      )
      .catch(() => {
        showToast("Rejection failed — please retry", "error");
        fetchPendingSpendApprovals();
      });
  };

  const handleApproveSupplier = (appId, skuCode, recId) => {
    const form = supplierAppForms[appId] || {};
    const quotedPrice  = parseFloat(form.quotedPrice);
    const leadTimeDays = parseInt(form.leadTime);
    if (!skuCode || !quotedPrice || !leadTimeDays || isNaN(quotedPrice) || isNaN(leadTimeDays)) {
      showToast("Enter valid quoted price and lead time", "error");
      return;
    }
    setSupplierAppForms((prev) => ({ ...prev, [appId]: { ...prev[appId], submitting: true } }));
    axios
      .post(
        `${API_BASE}/agents/supplier-applications/${appId}/approve`
        + `?tenant_id=${TENANT_ID}&sku_code=${encodeURIComponent(skuCode)}`
        + `&quoted_price=${quotedPrice}&lead_time_days=${leadTimeDays}`
      )
      .then(() => {
        // Immediately send RFQ to the newly onboarded vendor
        if (recId) {
          return axios
            .post(`${API_BASE}/agents/recommendations/${recId}/send-rfq?tenant_id=${TENANT_ID}`)
            .then(() => showToast(`Supplier approved — RFQ sent for ${skuCode}`))
            .catch(() => showToast(`Supplier approved — RFQ send failed (retry from Recommendations)`));
        }
        showToast(`Supplier approved — ${skuCode} now has a vendor`);
      })
      .then(() => {
        fetchRecommendations();
        fetchVendors();
      })
      .catch(() => {
        showToast("Approval failed — please retry", "error");
        setSupplierAppForms((prev) => ({ ...prev, [appId]: { ...prev[appId], submitting: false } }));
      });
  };

  const submitChat = async () => {
    if (!chatQuestion.trim()) return;
    setChatLoading(true);
    setChatAnswer(null);
    try {
      const res = await axios.post(`${API_BASE}/agents/chat?tenant_id=${TENANT_ID}`, {
        question: chatQuestion.trim(),
      });
      setChatAnswer(res.data.answer);
    } catch {
      setChatAnswer("Sorry, I couldn't process that. Please try again.");
    } finally {
      setChatLoading(false);
    }
  };

  const submitMeenaQuote = (recId) => {
    const form = meenaQuoteForms[recId] || {};
    const price = parseFloat(form.price);
    const lead  = parseInt(form.lead);
    if (!price || !lead || isNaN(price) || isNaN(lead)) {
      showToast("Enter valid price and lead time", "error");
      return;
    }
    // Optimistically remove from inbox so the card disappears immediately
    setRfqInbox((prev) => prev.filter((r) => r.recommendation_id !== recId));
    showToast("Proforma submitted — processing...");
    axios
      .post(`${API_BASE}/agents/recommendations/${recId}/meena-quote?tenant_id=${TENANT_ID}`, {
        unit_price:     price,
        lead_time_days: lead,
      })
      .then(() => {
        showToast("Proforma submitted — confirmation email sent");
        setMeenaQuoteForms((prev) => ({ ...prev, [recId]: { price: "", lead: "" } }));
        fetchOrders();
        fetchRfqInbox();
      })
      .catch(() => {
        showToast("Submission failed — please retry", "error");
        fetchRfqInbox(); // restore the card
      });
  };

  const submitDelivery = () => {
    const v = deliveryModal;
    if (!v) return;

    // Compute new score using delta approach so direction is always correct
    // regardless of any mismatch between stored score and formula value.
    const newTotal   = v.total_orders + 1;
    const newOnTime  = v.on_time_deliveries + (deliveryForm.was_on_time ? 1 : 0);
    const newQuality = v.quality_issues + (deliveryForm.had_quality_issue ? 1 : 0);
    const prevFormulaScore = v.total_orders === 0 ? 50.0
      : Math.max(0, Math.min(100, (v.on_time_deliveries / v.total_orders) * 70 + (1 - v.quality_issues / v.total_orders) * 30));
    const newFormulaScore  = Math.max(0, Math.min(100, (newOnTime / newTotal) * 70 + (1 - newQuality / newTotal) * 30));
    const optScore   = Math.round(Math.max(0, Math.min(100, v.score + (newFormulaScore - prevFormulaScore))) * 100) / 100;

    // Apply instantly to Rohan's vendor list
    setVendors((prev) =>
      prev.map((vendor) =>
        vendor.id === v.id
          ? { ...vendor, score: optScore, total_orders: newTotal,
              on_time_deliveries: newOnTime, quality_issues: newQuality }
          : vendor
      )
    );

    // Also update Meena's dashboard optimistically if this is her vendor
    if (meenaVendorMeta?.id === v.id) {
      setMeenaVendorMeta((prev) => ({
        ...prev,
        score:              optScore,
        total_orders:       newTotal,
        on_time_deliveries: newOnTime,
        quality_issues:     newQuality,
        on_time_rate:       parseFloat(((newOnTime / newTotal) * 100).toFixed(1)),
      }));
      // Prepend to Meena's delivery list + reliability trend chart
      const newDelivery = {
        delivered_at: new Date().toISOString(),
        was_on_time: deliveryForm.was_on_time,
        had_quality_issue: deliveryForm.had_quality_issue,
      };
      setDeliveryHistory((prev) => (prev ? [newDelivery, ...prev] : [newDelivery]));
    }
    // Bump counter so Harpreet's delivery dots re-fetch
    setDeliveryRefreshKey((k) => k + 1);

    setDeliveryModal(null);
    showToast(`Score updated to ${optScore.toFixed(1)}`);

    // Persist in background, reconcile on response
    axios
      .post(`${API_BASE}/vendors/${v.id}/record-delivery?tenant_id=${TENANT_ID}`, deliveryForm)
      .then((res) => {
        setVendors((prev) =>
          prev.map((vendor) => (vendor.id === v.id ? { ...vendor, ...res.data } : vendor))
        );
        // Sync Harpreet's vendor-comparison (and Meena's real score) from server
        fetchVendorComparison();
      })
      .catch(() => {
        setVendors((prev) => prev.map((vendor) => (vendor.id === v.id ? v : vendor)));
        if (meenaVendorMeta?.id === v.id) fetchVendorComparison();
        showToast("Failed to save delivery — score reverted", "error");
      });
  };

  // ── Derived values ────────────────────────────────────────────────────────
  const pendingCount = recommendations.filter((r) => r.status === "pending" || r.status === "no_vendor").length;

  // ── Harpreet analytics helpers ────────────────────────────────────────────

  // Spend line chart: [{month:"Jan", "Punjab Components House": 32457, ...}, ...]
  const spendLineData = spendData
    ? (() => {
        const byMonth = {};
        spendData.forEach((r) => {
          const key = `${r.year}-${String(r.month).padStart(2, "0")}`;
          if (!byMonth[key]) byMonth[key] = { month: MONTH_NAMES[r.month], _key: key };
          byMonth[key][r.vendor_name] = r.total_value;
        });
        return Object.values(byMonth).sort((a, b) => (a._key > b._key ? 1 : -1));
      })()
    : null;

  // Spend pie: [{name, value}]
  const spendPieData = spendData
    ? (() => {
        const totals = {};
        spendData.forEach((r) => {
          totals[r.vendor_name] = (totals[r.vendor_name] || 0) + r.total_value;
        });
        return Object.entries(totals).map(([name, value]) => ({ name, value }));
      })()
    : null;

  // Meena score trend from delivery history
  const mScoreTrend = deliveryHistory
    ? (() => {
        const byMonth = {};
        deliveryHistory.forEach((d) => {
          const dt  = new Date(d.delivered_at);
          const key = `${dt.getFullYear()}-${String(dt.getMonth() + 1).padStart(2, "0")}`;
          if (!byMonth[key]) byMonth[key] = { label: MONTH_NAMES[dt.getMonth() + 1], on_time: 0, total: 0 };
          byMonth[key].total++;
          if (d.was_on_time) byMonth[key].on_time++;
        });
        return Object.entries(byMonth)
          .sort(([a], [b]) => (a > b ? 1 : -1))
          .map(([, m]) => ({ month: m.label, score: Math.round((m.on_time / m.total) * 100) }));
      })()
    : [];

  // ════════════════════════════════════════════════════════════════════════
  return (
    <div className="min-h-screen bg-slate-50 flex flex-col" style={{ fontFamily: "Inter, system-ui, sans-serif" }}>

      {/* ── NAVBAR ───────────────────────────────────────────────────────── */}
      <nav className="bg-slate-950 sticky top-0 z-30 border-b border-slate-800/60">
        <div className="max-w-7xl mx-auto px-8 h-14 flex items-center justify-between gap-4">

          {/* Brand */}
          <div className="flex items-center gap-2.5">
            <LogoMark />
            <div className="flex items-baseline gap-2">
              <span className="text-white font-semibold text-sm tracking-tight leading-none">ChainFlow</span>
              <span className="text-slate-500 text-xs leading-none hidden sm:block">Harpreet Hosiery Works</span>
            </div>
          </div>

          {/* Persona switcher + tabs + watchdog */}
          <div className="flex items-center gap-0.5">

            {/* Persona bubbles */}
            {Object.entries(PERSONAS).map(([key, p]) => (
              <button
                key={key}
                onClick={() => switchPersona(key)}
                title={`${p.label} — ${p.role}`}
                className={`w-6 h-6 rounded-full text-white text-[10px] font-bold mr-0.5
                            transition-all flex items-center justify-center
                            ${persona === key
                              ? `${p.color} ring-2 ring-white ring-offset-1 ring-offset-slate-950`
                              : "bg-slate-700 hover:bg-slate-600"}`}
              >
                {p.initial}
              </button>
            ))}

            <div className="w-px h-4 bg-slate-700 mx-2" />

            {/* Tab bar — changes per persona */}
            {PERSONA_TABS[persona].map((tab) => (
              <button
                key={tab}
                onClick={() => setActiveTab(tab)}
                className={`relative px-3 py-1.5 rounded-md text-xs font-medium transition-colors ${
                  activeTab === tab
                    ? "bg-white/10 text-white"
                    : "text-slate-400 hover:text-slate-200 hover:bg-white/5"
                }`}
              >
                {tab}
                {tab === "Alerts" && alerts.length > 0 && (
                  <span className="ml-1.5 inline-flex items-center justify-center bg-red-500 text-white text-[10px] font-bold rounded-full w-4 h-4">
                    {alerts.length}
                  </span>
                )}
                {tab === "Recommendations" && (pendingCount + pendingSpendApprovals.length) > 0 && (
                  <span className="ml-1.5 inline-flex items-center justify-center bg-amber-500 text-white text-[10px] font-bold rounded-full w-4 h-4">
                    {pendingCount + pendingSpendApprovals.length}
                  </span>
                )}
                {tab === "RFQ Inbox" && rfqInbox.length > 0 && (
                  <span className="ml-1.5 inline-flex items-center justify-center bg-emerald-500 text-white text-[10px] font-bold rounded-full w-4 h-4">
                    {rfqInbox.length}
                  </span>
                )}
              </button>
            ))}

            {/* Watchdog — only show for Rohan */}
            {persona === "rohan" && (
              <>
                <div className="w-px h-4 bg-slate-700 mx-2" />
                <button
                  onClick={runWatchdog}
                  disabled={watchdogLoading}
                  className="flex items-center gap-1.5 px-3 py-1.5 bg-blue-600 hover:bg-blue-500
                             disabled:bg-slate-800 disabled:text-slate-500
                             text-white text-xs font-medium rounded-md transition-colors"
                >
                  {watchdogLoading && <Spinner className="w-3 h-3 border-blue-300" />}
                  {watchdogLoading ? "Analysing..." : "Run Watchdog"}
                </button>
              </>
            )}

            {/* Health status dot */}
            {systemHealth && (
              <div className="flex items-center gap-1.5 mr-2" title={`Services: ${systemHealth}`}>
                <div
                  className={`w-2 h-2 rounded-full flex-shrink-0 ${
                    systemHealth === "healthy" ? "bg-emerald-400 shadow-[0_0_6px_rgba(52,211,153,0.6)]" : "bg-red-500 animate-pulse"
                  }`}
                />
                <span className="text-[10px] text-slate-400 hidden sm:block">
                  {systemHealth === "healthy" ? "All services live" : systemHealth}
                </span>
              </div>
            )}

            {/* Persona label pill */}
            <div className={`ml-3 px-2 py-0.5 rounded-full text-[10px] font-semibold text-white ${PERSONAS[persona].color}`}>
              {PERSONAS[persona].label}
            </div>
          </div>
        </div>
      </nav>

      {/* ── MAIN ─────────────────────────────────────────────────────────── */}
      <main className={`flex-1 w-full max-w-7xl mx-auto px-8 py-8 ${persona === "rohan" ? "pb-28" : ""}`}>

        {loading && persona === "rohan" && <Skeletons />}

        {/* ══ ROHAN: ALERTS ══════════════════════════════════════════════ */}
        {!loading && persona === "rohan" && activeTab === "Alerts" && (
          <div>
            <div className="mb-6">
              <h2 className="text-slate-900 font-semibold text-sm">Stock Alerts</h2>
              <p className="text-slate-500 text-xs mt-0.5">
                {alerts.length === 0
                  ? "All SKUs are above reorder threshold"
                  : `${alerts.length} SKU${alerts.length !== 1 ? "s" : ""} require attention`}
              </p>
            </div>

            {alerts.length === 0 ? (
              <div className="bg-emerald-50 border border-emerald-100 rounded-xl px-6 py-12 text-center">
                <p className="text-emerald-700 font-medium text-sm">All stock levels are healthy</p>
                <p className="text-emerald-600 text-xs mt-1 opacity-70">No SKUs below reorder threshold</p>
              </div>
            ) : (
              <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                {alerts.map((sku) => (
                  <div key={sku.id} className="bg-white rounded-xl border border-slate-200 p-5 hover:border-slate-300 transition-colors">
                    <div className="flex items-start justify-between mb-4">
                      <div>
                        <p className="font-mono font-semibold text-slate-900 text-sm">{sku.sku_code}</p>
                        <p className="text-slate-400 text-xs mt-0.5">{sku.name}</p>
                      </div>
                      <div className="flex flex-col items-end gap-1">
                        <span className={`px-2 py-0.5 rounded text-[11px] font-semibold uppercase tracking-wider ${statusStyles[sku.stock_status]}`}>
                          {sku.stock_status}
                        </span>
                        {sku.reorder_pending && (
                          <span className="px-2 py-0.5 rounded text-[11px] font-semibold uppercase tracking-wider bg-blue-50 text-blue-600 border border-blue-200">
                            Reorder Placed
                          </span>
                        )}
                      </div>
                    </div>

                    <div className="mb-4">
                      <div className="flex justify-between text-xs mb-1.5">
                        <span className="font-medium text-slate-700">{sku.current_quantity} {sku.unit}</span>
                        <span className="text-slate-400">threshold {sku.reorder_threshold} {sku.unit}</span>
                      </div>
                      <div className="w-full bg-slate-100 rounded-full h-1">
                        <div
                          className={`h-1 rounded-full ${barColor(sku.stock_status)}`}
                          style={{ width: `${Math.min(100, (sku.current_quantity / sku.reorder_threshold) * 100)}%` }}
                        />
                      </div>
                    </div>

                    <div className="flex items-center justify-between">
                      <span className="text-xs text-slate-500">
                        Reorder <span className="font-medium text-slate-700">{sku.reorder_quantity} {sku.unit}</span>
                      </span>
                      <span className="text-[11px] text-slate-400 bg-slate-50 border border-slate-200 px-2 py-0.5 rounded">
                        {sku.category}
                      </span>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}

        {/* ══ ROHAN: RECOMMENDATIONS ════════════════════════════════════ */}
        {!loading && persona === "rohan" && activeTab === "Recommendations" && (
          <div>
            <div className="mb-6">
              <h2 className="text-slate-900 font-semibold text-sm">Reorder Recommendations</h2>
              <p className="text-slate-500 text-xs mt-0.5">
                {pendingCount === 0 ? "No pending recommendations" : `${pendingCount} pending review`}
              </p>
            </div>

            {streamDrawerOpen && (
              <div className="bg-slate-900 rounded-xl border border-slate-700 p-4 mb-4">
                <div className="flex items-center justify-between mb-2">
                  <div className="flex items-center gap-2">
                    {!streamingDone && <Spinner className="w-3 h-3 border-blue-400" />}
                    <span className={`text-[11px] font-semibold ${streamingDone ? "text-emerald-400" : "text-blue-400"}`}>
                      {streamingDone ? "gpt-oss-120b complete" : "gpt-oss-120b thinking..."}
                    </span>
                  </div>
                  {streamingDone && (
                    <button onClick={() => setStreamDrawerOpen(false)} className="text-slate-500 hover:text-slate-300 text-[11px]">
                      close
                    </button>
                  )}
                </div>
                <pre className="font-mono text-xs text-slate-300 whitespace-pre-wrap break-words leading-relaxed max-h-48 overflow-y-auto">
                  {streamText || " "}
                </pre>
              </div>
            )}

            {recommendations.length === 0 && !watchdogLoading ? (
              <div className="bg-white border border-slate-200 rounded-xl px-6 py-12 text-center">
                <p className="text-slate-600 font-medium text-sm">No recommendations yet</p>
                <p className="text-slate-400 text-xs mt-1">Run the watchdog to generate AI-powered reorder suggestions</p>
              </div>
            ) : (
              <div className="space-y-3">
                {recommendations.map((rec) => {
                  const pickedVendorId = recVendorPicks[rec.id] ?? rec.vendor_id;
                  const pickedVendor   = (rec.available_vendors || []).find((v) => v.vendor_id === pickedVendorId)
                                         || { vendor_name: rec.vendor_name };
                  return (
                  <div key={rec.id} className={`bg-white rounded-xl border p-5 ${rec.status === "no_vendor" ? "border-orange-300" : "border-slate-200"}`}>
                    <div className="flex items-start justify-between mb-3">
                      <div>
                        <p className="font-mono font-semibold text-slate-900 text-sm">{rec.sku_code}</p>
                        <p className="text-slate-400 text-xs mt-0.5">
                          {rec.status === "no_vendor"
                            ? `${rec.sku_name} · ${rec.quantity.toLocaleString()} ${rec.unit || "units"}`
                            : `${pickedVendor.vendor_name} · ${rec.quantity.toLocaleString()} ${rec.unit || "units"}`}
                        </p>
                      </div>
                      <span className={`px-2 py-0.5 rounded text-[11px] font-semibold uppercase tracking-wider ${statusStyles[rec.status] || statusStyles.pending}`}>
                        {rec.status === "no_vendor" ? "No Vendor" : rec.status}
                      </span>
                    </div>

                    <p className="text-slate-500 text-xs leading-relaxed border-l-2 border-slate-200 pl-3 mb-4">
                      {rec.reasoning}
                    </p>

                    {rec.status === "no_vendor" && (rec.supplier_applications || []).length > 0 && (
                      <div className="mt-2 space-y-3">
                        <p className="text-[10px] text-orange-700 uppercase tracking-wide font-semibold">
                          Pending Supplier Application{rec.supplier_applications.length !== 1 ? "s" : ""}
                        </p>
                        {rec.supplier_applications.map((app) => {
                          const af = supplierAppForms[app.id] || {};
                          return (
                            <div key={app.id} className="bg-orange-50 border border-orange-200 rounded-lg p-3">
                              <div className="flex items-start justify-between mb-2">
                                <div>
                                  <p className="font-semibold text-slate-800 text-xs">{app.business_name}</p>
                                  <p className="text-slate-500 text-[11px]">{app.contact_name} · {app.city}</p>
                                </div>
                                <div className="text-right text-[11px] text-slate-500">
                                  <p>Lead: {app.avg_lead_time_days}d</p>
                                  {app.min_order_value_inr && <p>MOV: {formatINR(app.min_order_value_inr)}</p>}
                                </div>
                              </div>
                              <div className="flex gap-2 items-end mt-2">
                                <div className="flex-1">
                                  <label className="text-[10px] text-slate-500 block mb-0.5">Quoted Price (₹/{rec.unit})</label>
                                  <input
                                    type="number"
                                    placeholder="e.g. 45"
                                    value={af.quotedPrice || ""}
                                    onChange={(e) => setSupplierAppForms((prev) => ({ ...prev, [app.id]: { ...prev[app.id], quotedPrice: e.target.value } }))}
                                    className="w-full text-xs border border-slate-200 rounded px-2 py-1.5 focus:outline-none focus:ring-1 focus:ring-orange-400"
                                  />
                                </div>
                                <div className="flex-1">
                                  <label className="text-[10px] text-slate-500 block mb-0.5">Lead Time (days)</label>
                                  <input
                                    type="number"
                                    placeholder="e.g. 8"
                                    value={af.leadTime || ""}
                                    onChange={(e) => setSupplierAppForms((prev) => ({ ...prev, [app.id]: { ...prev[app.id], leadTime: e.target.value } }))}
                                    className="w-full text-xs border border-slate-200 rounded px-2 py-1.5 focus:outline-none focus:ring-1 focus:ring-orange-400"
                                  />
                                </div>
                                <button
                                  onClick={() => handleApproveSupplier(app.id, rec.sku_code, rec.id)}
                                  disabled={af.submitting}
                                  className="px-3 py-1.5 bg-orange-600 hover:bg-orange-500 disabled:bg-slate-300 text-white text-xs font-medium rounded-lg transition-colors whitespace-nowrap"
                                >
                                  {af.submitting ? "Approving..." : "Approve & Send RFQ"}
                                </button>
                              </div>
                            </div>
                          );
                        })}
                      </div>
                    )}

                    {rec.status === "no_vendor" && (rec.supplier_applications || []).length === 0 && (
                      <p className="text-orange-600 text-xs italic">No supplier applications found. Ask vendors to apply before reordering.</p>
                    )}

                    {rec.status === "pending" && (
                      <>
                        {(rec.available_vendors || []).length > 1 && (
                          <div className="mb-3">
                            <p className="text-[10px] text-slate-400 uppercase tracking-wide font-medium mb-1.5">Vendor</p>
                            <div className="flex flex-wrap gap-1.5">
                              {rec.available_vendors.map((v) => (
                                <button
                                  key={v.vendor_id}
                                  onClick={() => setRecVendorPicks((prev) => ({ ...prev, [rec.id]: v.vendor_id }))}
                                  className={`px-2.5 py-1.5 rounded-lg text-xs font-medium border transition-colors ${
                                    pickedVendorId === v.vendor_id
                                      ? "bg-slate-900 text-white border-slate-900"
                                      : "bg-white text-slate-600 border-slate-200 hover:border-slate-400"
                                  }`}
                                >
                                  {v.vendor_name}
                                  <span className="ml-1.5 opacity-60">
                                    {v.score}/100 · ₹{v.quoted_price}/{rec.unit}
                                  </span>
                                </button>
                              ))}
                            </div>
                          </div>
                        )}
                        <div className="flex gap-2">
                          <button
                            onClick={() => handleApprove(rec.id)}
                            className="flex-1 py-2 bg-slate-900 hover:bg-slate-700 text-white text-xs font-medium rounded-lg transition-colors"
                          >
                            Place Order
                          </button>
                          <button
                            onClick={() => handleReject(rec.id)}
                            className="flex-1 py-2 border border-slate-200 hover:bg-slate-50 text-slate-600 text-xs font-medium rounded-lg transition-colors"
                          >
                            Reject
                          </button>
                        </div>
                      </>
                    )}
                  </div>
                  );
                })}
              </div>
            )}

            {/* ── Spend Approvals ─────────────────────────────────────── */}
            {pendingSpendApprovals.length > 0 && (
              <div className="mt-8">
                <div className="mb-4">
                  <h3 className="text-slate-900 font-semibold text-sm">Spend Approvals</h3>
                  <p className="text-slate-500 text-xs mt-0.5">
                    {pendingSpendApprovals.length} order{pendingSpendApprovals.length !== 1 ? "s" : ""} require your sign-off before a PO can be issued
                  </p>
                </div>
                <div className="space-y-3">
                  {pendingSpendApprovals.map((rec) => {
                    const rejectForm = spendRejectForms[rec.recommendation_id] || {};
                    const tierStyle = rec.policy_tier === "harpreet"
                      ? "bg-red-50 text-red-700 ring-1 ring-inset ring-red-200"
                      : "bg-amber-50 text-amber-700 ring-1 ring-inset ring-amber-200";
                    return (
                      <div key={rec.recommendation_id} className="bg-white rounded-xl border border-amber-200 p-5">
                        <div className="flex items-start justify-between mb-3">
                          <div>
                            <p className="font-mono font-semibold text-slate-900 text-sm">{rec.sku_code}</p>
                            <p className="text-slate-400 text-xs mt-0.5">
                              {rec.sku_name} · {rec.quantity.toLocaleString()} {rec.unit}
                            </p>
                          </div>
                          <span className={`px-2 py-0.5 rounded text-[11px] font-semibold uppercase tracking-wider ${tierStyle}`}>
                            {rec.policy_tier === "harpreet" ? "Harpreet Tier" : "Spend Hold"}
                          </span>
                        </div>

                        <div className="grid grid-cols-2 gap-3 bg-slate-50 rounded-lg p-3 mb-4 text-xs">
                          <div>
                            <p className="text-[10px] text-slate-400 uppercase tracking-wide font-medium">Vendor</p>
                            <p className="font-semibold text-slate-800 mt-0.5">{rec.vendor_name}</p>
                          </div>
                          <div>
                            <p className="text-[10px] text-slate-400 uppercase tracking-wide font-medium">Order Value</p>
                            <p className="font-semibold text-slate-800 mt-0.5">{formatINR(rec.order_value)}</p>
                          </div>
                        </div>

                        {rejectForm.show ? (
                          <div className="space-y-2">
                            <input
                              type="text"
                              placeholder="Reason for rejection (optional)"
                              value={rejectForm.reason || ""}
                              onChange={(e) =>
                                setSpendRejectForms((prev) => ({
                                  ...prev,
                                  [rec.recommendation_id]: { ...prev[rec.recommendation_id], reason: e.target.value },
                                }))
                              }
                              className="w-full border border-slate-200 rounded-lg px-3 py-2 text-xs focus:outline-none focus:ring-2 focus:ring-slate-300"
                            />
                            <div className="flex gap-2">
                              <button
                                onClick={() => handleRejectSpend(rec.recommendation_id, rejectForm.reason || "")}
                                className="flex-1 py-2 bg-red-600 hover:bg-red-500 text-white text-xs font-medium rounded-lg transition-colors"
                              >
                                Confirm Reject
                              </button>
                              <button
                                onClick={() =>
                                  setSpendRejectForms((prev) => ({
                                    ...prev,
                                    [rec.recommendation_id]: { show: false, reason: "" },
                                  }))
                                }
                                className="flex-1 py-2 border border-slate-200 hover:bg-slate-50 text-slate-600 text-xs font-medium rounded-lg transition-colors"
                              >
                                Cancel
                              </button>
                            </div>
                          </div>
                        ) : (
                          <div className="flex gap-2">
                            <button
                              onClick={() => handleApproveSpend(rec.recommendation_id)}
                              className="flex-1 py-2 bg-slate-900 hover:bg-slate-700 text-white text-xs font-medium rounded-lg transition-colors"
                            >
                              Approve & Issue PO
                            </button>
                            <button
                              onClick={() =>
                                setSpendRejectForms((prev) => ({
                                  ...prev,
                                  [rec.recommendation_id]: { show: true, reason: "" },
                                }))
                              }
                              className="flex-1 py-2 border border-red-200 hover:bg-red-50 text-red-600 text-xs font-medium rounded-lg transition-colors"
                            >
                              Reject
                            </button>
                          </div>
                        )}
                      </div>
                    );
                  })}
                </div>
              </div>
            )}
          </div>
        )}

        {/* ══ ROHAN: VENDORS ════════════════════════════════════════════ */}
        {!loading && persona === "rohan" && activeTab === "Vendors" && (
          <div>
            <div className="mb-6">
              <h2 className="text-slate-900 font-semibold text-sm">Vendors</h2>
              <p className="text-slate-500 text-xs mt-0.5">{vendors.length} registered vendors</p>
            </div>

            <div className="space-y-3">
              {vendors.map((v) => (
                <div key={v.id} className="bg-white rounded-xl border border-slate-200 p-5">
                  <div className="flex items-start justify-between mb-4">
                    <div>
                      <p className="font-semibold text-slate-900 text-sm">{v.name}</p>
                      <p className="text-slate-400 text-xs mt-0.5">{v.city} · {v.contact_name}</p>
                      {meenaVendorMeta?.id === v.id && (
                        <span className="inline-flex items-center gap-1 mt-1 text-[10px] font-semibold text-emerald-700">
                          <span className="w-3.5 h-3.5 rounded-full bg-emerald-600 text-white flex items-center justify-center text-[8px] font-bold">M</span>
                          Meena's company
                        </span>
                      )}
                    </div>
                    <span className="text-[11px] text-slate-500 bg-slate-50 border border-slate-200 px-2 py-0.5 rounded">
                      {v.materials_supplied}
                    </span>
                  </div>

                  <div className="mb-4">
                    <div className="flex justify-between items-center mb-1.5">
                      <span className="text-xs text-slate-500">Performance score</span>
                      <span className={`font-semibold text-sm ${scoreColor(v.score)}`}>{v.score.toFixed(1)}</span>
                    </div>
                    <div className="w-full bg-slate-100 rounded-full h-1">
                      <div className={`h-1 rounded-full ${scoreBar(v.score)}`} style={{ width: `${v.score}%` }} />
                    </div>
                  </div>

                  <div className="flex items-center justify-between">
                    <div className="flex gap-4 text-xs text-slate-500">
                      <span>Orders <span className="font-semibold text-slate-700">{v.total_orders}</span></span>
                      <span>On-time <span className="font-semibold text-slate-700">{v.on_time_rate != null ? `${v.on_time_rate}%` : "—"}</span></span>
                    </div>
                    <button
                      onClick={() => {
                        if (deliveryModal?.id === v.id) {
                          setDeliveryModal(null);
                        } else {
                          setDeliveryModal(v);
                          setDeliveryForm({ was_on_time: true, had_quality_issue: false });
                        }
                      }}
                      className={`px-3 py-1.5 text-xs font-medium rounded-lg transition-colors ${
                        deliveryModal?.id === v.id
                          ? "bg-slate-200 text-slate-700 hover:bg-slate-300"
                          : "bg-slate-900 hover:bg-slate-700 text-white"
                      }`}
                    >
                      {deliveryModal?.id === v.id ? "Cancel" : "Record Delivery"}
                    </button>
                  </div>

                  {deliveryModal?.id === v.id && (
                    <div className="mt-4 pt-4 border-t border-slate-100 space-y-2">
                      <div className="flex items-center justify-between bg-slate-50 rounded-lg px-4 py-3">
                        <span className="text-sm text-slate-700">Delivered on time?</span>
                        <Toggle
                          checked={deliveryForm.was_on_time}
                          onChange={() => setDeliveryForm((f) => ({ ...f, was_on_time: !f.was_on_time }))}
                        />
                      </div>
                      <div className="flex items-center justify-between bg-slate-50 rounded-lg px-4 py-3">
                        <span className="text-sm text-slate-700">Quality issue?</span>
                        <Toggle
                          checked={deliveryForm.had_quality_issue}
                          onChange={() => setDeliveryForm((f) => ({ ...f, had_quality_issue: !f.had_quality_issue }))}
                          danger
                        />
                      </div>
                      <button
                        onClick={submitDelivery}
                        className="w-full py-2 mt-1 bg-slate-900 hover:bg-slate-700 text-white text-sm font-medium rounded-lg transition-colors"
                      >
                        Save Delivery
                      </button>
                    </div>
                  )}
                </div>
              ))}
            </div>
          </div>
        )}

        {/* ══ ROHAN: PROCUREMENT KANBAN ════════════════════════════════ */}
        {!loading && persona === "rohan" && activeTab === "Procurement" && (
          <div>
            <div className="flex items-center justify-between mb-6">
              <div>
                <h2 className="text-slate-900 font-semibold text-sm">Procurement Pipeline</h2>
                <p className="text-slate-500 text-xs mt-0.5">
                  {orders.length} order{orders.length !== 1 ? "s" : ""} in pipeline · auto-refreshes every 10s
                </p>
              </div>
              <button
                onClick={() => fetchOrders().catch(() => {})}
                className="px-3 py-1.5 border border-slate-200 hover:bg-slate-50 text-slate-600 text-xs font-medium rounded-lg transition-colors"
              >
                Refresh
              </button>
            </div>

            {orders.length === 0 ? (
              <div className="bg-white border border-slate-200 rounded-xl px-6 py-12 text-center">
                <p className="text-slate-600 font-medium text-sm">No orders in pipeline</p>
                <p className="text-slate-400 text-xs mt-1">Approve a recommendation and send an RFQ to get started</p>
              </div>
            ) : (() => {
              const STAGES = [
                { key: "rfq_sent",        label: "RFQ Sent",     color: "border-t-blue-500"    },
                { key: "quote_received",  label: "Quote In",     color: "border-t-violet-500"  },
                { key: "po_issued",       label: "PO Issued",    color: "border-t-amber-500"   },
                { key: "invoice_received",label: "Invoiced",     color: "border-t-emerald-500" },
              ];
              const byStage = {};
              STAGES.forEach((s) => { byStage[s.key] = []; });
              orders.forEach((o) => {
                if (byStage[o.status] !== undefined) byStage[o.status].push(o);
              });
              return (
                <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                  {STAGES.map((stage) => (
                    <div key={stage.key} className={`bg-white rounded-xl border-t-4 border border-slate-200 ${stage.color}`}>
                      <div className="px-4 py-3 border-b border-slate-100">
                        <p className="text-xs font-semibold text-slate-700">{stage.label}</p>
                        <p className="text-[10px] text-slate-400 mt-0.5">{byStage[stage.key].length} order{byStage[stage.key].length !== 1 ? "s" : ""}</p>
                      </div>
                      <div className="p-3 space-y-2 min-h-[120px]">
                        {byStage[stage.key].map((o) => (
                          <div key={o.recommendation_id} className="bg-slate-50 rounded-lg p-3 border border-slate-100">
                            <p className="font-mono font-semibold text-slate-800 text-xs">{o.sku_code}</p>
                            <p className="text-slate-500 text-[11px] mt-0.5">{o.vendor_name}</p>
                            <div className="flex justify-between items-center mt-2 text-[10px] text-slate-400">
                              <span>{o.quantity?.toLocaleString()} {o.unit}</span>
                              {o.total_value ? <span className="font-semibold text-slate-600">{formatINR(o.total_value)}</span> : null}
                            </div>
                            {o.updated_at && (
                              <p className="text-[10px] text-slate-300 mt-1">{formatDate(o.updated_at)}</p>
                            )}
                          </div>
                        ))}
                        {byStage[stage.key].length === 0 && (
                          <p className="text-[11px] text-slate-300 text-center py-4">Empty</p>
                        )}
                      </div>
                    </div>
                  ))}
                </div>
              );
            })()}
          </div>
        )}

        {/* ══ HARPREET: OVERVIEW ════════════════════════════════════════ */}
        {persona === "harpreet" && activeTab === "Overview" && (() => {
          const inventoryValue = alerts.reduce(
            (s, sku) => s + sku.current_quantity * sku.unit_cost, 0
          );
          const criticalCount   = alerts.filter((s) => s.stock_status === "critical").length;
          const pendingApprovals = recHistory.filter((r) => r.status === "pending").length;
          const mtdSpend = spendData
            ? spendData.filter((r) => r.month === 6).reduce((s, r) => s + r.total_value, 0)
            : null;
          const estimatedSavings = criticalCount * 25000;

          return (
            <div>
              <SectionHeader title="Business Overview" sub="Harpreet Hosiery Works · Ludhiana · Live snapshot" />

              {/* KPI row */}
              <div className="grid grid-cols-2 md:grid-cols-5 gap-3 mb-6">
                <KpiCard label="Inventory Value"  value={formatINR(inventoryValue)}         accent="blue"   />
                <KpiCard label="SKUs at Risk"     value={alerts.length}   sub={`${criticalCount} critical`} accent="red"    />
                <KpiCard label="Active Vendors"   value={vendorComparison ? vendorComparison.length : "—"}  accent="violet" />
                <KpiCard label="MTD Spend"        value={mtdSpend !== null ? formatINR(mtdSpend) : "—"} sub="Jun 2025" accent="amber" />
                <KpiCard label="Est. Savings"     value={formatINR(estimatedSavings)} sub={`${criticalCount} stockouts prevented`} accent="green" />
              </div>

              <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-4">
                {/* Stock health bars */}
                <div className="bg-white rounded-xl border border-slate-200 p-5">
                  <p className="text-xs font-semibold text-slate-700 mb-4">Stock Health</p>
                  {alerts.length === 0 ? (
                    <p className="text-xs text-slate-400 text-center py-8">No alerts</p>
                  ) : (
                    <div className="space-y-3">
                      {alerts.map((sku) => {
                        const pct = Math.min(100, (sku.current_quantity / sku.reorder_threshold) * 100);
                        return (
                          <div key={sku.id}>
                            <div className="flex justify-between text-[11px] mb-1">
                              <span className="font-mono text-slate-700">{sku.sku_code}</span>
                              <span className="text-slate-400">{pct.toFixed(0)}%</span>
                            </div>
                            <div className="w-full bg-slate-100 rounded-full h-2">
                              <div className={`h-2 rounded-full ${barColor(sku.stock_status)}`} style={{ width: `${pct}%` }} />
                            </div>
                          </div>
                        );
                      })}
                    </div>
                  )}
                </div>

                {/* Value at risk */}
                <div className="bg-white rounded-xl border border-slate-200 p-5">
                  <p className="text-xs font-semibold text-slate-700 mb-4">Value at Risk</p>
                  {criticalCount === 0 ? (
                    <p className="text-xs text-slate-400 text-center py-8">No critical SKUs</p>
                  ) : (
                    <div className="space-y-3">
                      {alerts
                        .filter((s) => s.stock_status === "critical")
                        .slice(0, 4)
                        .map((sku) => {
                          const dailyDrop = (sku.reorder_threshold * 0.8) / 30;
                          const daysLeft  = dailyDrop > 0 ? Math.round(sku.current_quantity / dailyDrop) : "?";
                          const value     = sku.current_quantity * sku.unit_cost;
                          return (
                            <div key={sku.id} className="bg-red-50 border border-red-100 rounded-lg p-3">
                              <div className="flex justify-between items-start">
                                <div>
                                  <p className="font-mono font-semibold text-red-800 text-xs">{sku.sku_code}</p>
                                  <p className="text-red-600 text-[11px] mt-0.5">~{daysLeft} days remaining</p>
                                </div>
                                <p className="text-xs font-semibold text-red-700">{formatINR(value)}</p>
                              </div>
                            </div>
                          );
                        })}
                    </div>
                  )}
                </div>
              </div>

              {/* Pending approvals */}
              {pendingApprovals > 0 && (
                <div className="bg-white rounded-xl border border-amber-200 p-5">
                  <p className="text-xs font-semibold text-amber-700 mb-3">
                    ⏳ {pendingApprovals} Pending Approval{pendingApprovals !== 1 ? "s" : ""}
                  </p>
                  <div className="space-y-2">
                    {recHistory.filter((r) => r.status === "pending").map((rec) => (
                      <div key={rec.id} className="flex items-center justify-between bg-amber-50 rounded-lg px-3 py-2">
                        <div>
                          <span className="font-mono text-xs font-semibold text-slate-800">{rec.sku_code}</span>
                          <span className="text-xs text-slate-500 ml-2">→ {rec.vendor_name}</span>
                        </div>
                        <span className="text-xs text-amber-600">{rec.quantity.toLocaleString()} units</span>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>
          );
        })()}

        {/* ══ HARPREET: SPEND ═══════════════════════════════════════════ */}
        {persona === "harpreet" && activeTab === "Spend" && (
          <div>
            <SectionHeader title="Spend Analysis" sub="Jan – Jun 2025 · All vendors" />

            <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-4">

              {/* Line chart */}
              <div className="bg-white rounded-xl border border-slate-200 p-5">
                <p className="text-xs font-semibold text-slate-700 mb-4">Monthly Spend by Vendor</p>
                {!spendLineData ? (
                  <ChartSkeleton />
                ) : (
                  <ResponsiveContainer width="100%" height={240}>
                    <LineChart data={spendLineData} margin={{ top: 5, right: 10, bottom: 5, left: 10 }}>
                      <CartesianGrid strokeDasharray="3 3" stroke="#f1f5f9" />
                      <XAxis dataKey="month" tick={{ fontSize: 11, fill: "#94a3b8" }} />
                      <YAxis tick={{ fontSize: 11, fill: "#94a3b8" }} tickFormatter={(v) => `₹${(v / 1000).toFixed(0)}k`} />
                      <Tooltip formatter={(v, name) => [formatINR(v), name.split(" ")[0]]} contentStyle={{ fontSize: 11, borderRadius: 8 }} />
                      <Legend wrapperStyle={{ fontSize: 11 }} formatter={(name) => name.split(" ")[0]} />
                      {Object.keys(VENDOR_COLORS).map((name) => (
                        <Line key={name} type="monotone" dataKey={name} stroke={VENDOR_COLORS[name]} strokeWidth={2} dot={false} connectNulls />
                      ))}
                    </LineChart>
                  </ResponsiveContainer>
                )}
              </div>

              {/* Pie chart */}
              <div className="bg-white rounded-xl border border-slate-200 p-5">
                <p className="text-xs font-semibold text-slate-700 mb-4">Spend Distribution</p>
                {!spendPieData ? (
                  <ChartSkeleton />
                ) : (
                  <ResponsiveContainer width="100%" height={240}>
                    <PieChart>
                      <Pie
                        data={spendPieData} dataKey="value" nameKey="name"
                        cx="50%" cy="50%" outerRadius={85}
                        label={({ name, percent }) => `${name.split(" ")[0]} ${(percent * 100).toFixed(0)}%`}
                        labelLine={false}
                        style={{ fontSize: 11 }}
                      >
                        {spendPieData.map((_, i) => (
                          <Cell key={i} fill={Object.values(VENDOR_COLORS)[i % 3]} />
                        ))}
                      </Pie>
                      <Tooltip formatter={(v) => formatINR(v)} contentStyle={{ fontSize: 11, borderRadius: 8 }} />
                    </PieChart>
                  </ResponsiveContainer>
                )}
              </div>
            </div>

            {/* Spend table */}
            {spendData && (
              <div className="bg-white rounded-xl border border-slate-200 p-5 mb-4">
                <p className="text-xs font-semibold text-slate-700 mb-4">Total by Vendor</p>
                <div className="space-y-2">
                  {spendPieData && spendPieData
                    .sort((a, b) => b.value - a.value)
                    .map((row) => (
                      <div key={row.name} className="flex items-center justify-between bg-slate-50 rounded-lg px-4 py-2.5">
                        <div className="flex items-center gap-2">
                          <div
                            className="w-2.5 h-2.5 rounded-full flex-shrink-0"
                            style={{ background: VENDOR_COLORS[row.name] || "#94a3b8" }}
                          />
                          <span className="text-xs text-slate-700">{row.name}</span>
                        </div>
                        <span className="text-xs font-semibold text-slate-900">{formatINR(row.value)}</span>
                      </div>
                    ))}
                </div>
              </div>
            )}

            {/* Seasonal banner */}
            <div className="bg-blue-50 border border-blue-200 rounded-xl px-5 py-4">
              <p className="text-xs font-semibold text-blue-800">📅 Peak Season Alert</p>
              <p className="text-xs text-blue-600 mt-1">
                Peak production season begins in approximately 26 weeks (September).
                Consider building buffer stock for ELASTIC-25MM and COTTON-YARN-2PLY ahead of high-demand period.
              </p>
            </div>
          </div>
        )}

        {/* ══ HARPREET: VENDOR HEALTH ═══════════════════════════════════ */}
        {persona === "harpreet" && activeTab === "Vendor Health" && (
          <HarpreetVendorHealth vendorComparison={vendorComparison} refreshTrigger={deliveryRefreshKey} />
        )}

        {/* ══ MEENA: MY PERFORMANCE ═════════════════════════════════════ */}
        {persona === "meena" && activeTab === "My Performance" && (() => {
          const v = meenaVendorMeta;
          if (!v) {
            return (
              <div className="space-y-3">
                <ChartSkeleton height={80} />
                <ChartSkeleton />
              </div>
            );
          }

          return (
            <div>
              <SectionHeader title="My Performance" sub="Punjab Components House · Supplier Dashboard" />

              {/* KPI row */}
              <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-6">
                <KpiCard label="My Score"       value={`${v.score.toFixed(1)}/100`}   accent="blue"   />
                <KpiCard label="On-Time Rate"   value={`${v.on_time_rate}%`}           accent="green"  />
                <KpiCard label="Total Orders"   value={v.total_orders}                 accent="violet" />
                <KpiCard label="Quality Issues" value={v.quality_issues} accent={v.quality_issues > 2 ? "red" : "green"} />
              </div>

              {/* Industry benchmark */}
              <div className="bg-slate-50 border border-slate-200 rounded-xl px-5 py-4 mb-4">
                <p className="text-xs font-semibold text-slate-700 mb-2">How you compare</p>
                <div className="grid grid-cols-3 gap-4 text-center text-xs">
                  <div>
                    <p className="text-slate-500">Your lead time</p>
                    <p className="font-semibold text-slate-900 mt-0.5">{v.lead_time_days} days</p>
                  </div>
                  <div>
                    <p className="text-slate-500">Category average</p>
                    <p className="font-semibold text-slate-400 mt-0.5">9.2 days</p>
                  </div>
                  <div>
                    <p className={`font-medium mt-0.5 ${v.lead_time_days < 9.2 ? "text-emerald-600" : "text-amber-600"}`}>
                      {v.lead_time_days < 9.2 ? "✓ Faster than average" : "Slower than average"}
                    </p>
                  </div>
                </div>
              </div>

              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">

                {/* Score trend */}
                <div className="bg-white rounded-xl border border-slate-200 p-5">
                  <p className="text-xs font-semibold text-slate-700 mb-4">Reliability Trend</p>
                  {mScoreTrend.length === 0 ? (
                    <ChartSkeleton height={200} />
                  ) : (
                    <ResponsiveContainer width="100%" height={200}>
                      <LineChart data={mScoreTrend}>
                        <CartesianGrid strokeDasharray="3 3" stroke="#f1f5f9" />
                        <XAxis dataKey="month" tick={{ fontSize: 11, fill: "#94a3b8" }} />
                        <YAxis domain={[0, 100]} tick={{ fontSize: 11, fill: "#94a3b8" }} />
                        <Tooltip contentStyle={{ fontSize: 11, borderRadius: 8 }} formatter={(v) => [`${v}%`, "On-time rate"]} />
                        <ReferenceLine y={90} stroke="#10b981" strokeDasharray="4 4"
                          label={{ value: "target", position: "right", fontSize: 10, fill: "#10b981" }} />
                        <Line type="monotone" dataKey="score" stroke="#3b82f6" strokeWidth={2} dot={{ r: 4 }} />
                      </LineChart>
                    </ResponsiveContainer>
                  )}
                </div>

                {/* Recent deliveries */}
                <div className="bg-white rounded-xl border border-slate-200 p-5">
                  <p className="text-xs font-semibold text-slate-700 mb-4">Recent Deliveries</p>
                  {deliveryHistory === null ? (
                    <ChartSkeleton height={200} />
                  ) : deliveryHistory.length === 0 ? (
                    <p className="text-xs text-slate-400 text-center py-8">No delivery history</p>
                  ) : (
                    <div className="space-y-1.5">
                      {deliveryHistory.slice(0, 8).map((d, i) => (
                        <div
                          key={i}
                          className={`flex items-center justify-between rounded-lg px-3 py-2 text-xs ${
                            d.had_quality_issue ? "bg-red-50" : d.was_on_time ? "bg-emerald-50" : "bg-amber-50"
                          }`}
                        >
                          <span className="text-slate-500">{formatDate(d.delivered_at)}</span>
                          <div className="flex gap-2">
                            <span className={d.was_on_time ? "text-emerald-600" : "text-amber-600"}>
                              {d.was_on_time ? "On time" : "Late"}
                            </span>
                            {d.had_quality_issue && <span className="text-red-600">Quality issue</span>}
                          </div>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              </div>
            </div>
          );
        })()}

        {/* ══ MEENA: RFQ INBOX ══════════════════════════════════════════ */}
        {persona === "meena" && activeTab === "RFQ Inbox" && (
          <div>
            <SectionHeader
              title="RFQ Inbox"
              sub={rfqInbox.length === 0 ? "No pending quote requests" : `${rfqInbox.length} item${rfqInbox.length !== 1 ? "s" : ""} awaiting your quote`}
            />

            {rfqInbox.length === 0 ? (
              <div className="bg-white border border-slate-200 rounded-xl px-6 py-12 text-center">
                <p className="text-slate-600 font-medium text-sm">No open RFQs</p>
                <p className="text-slate-400 text-xs mt-1">
                  When Rohan sends an RFQ your way, it will appear here to quote on
                </p>
              </div>
            ) : (
              <div className="space-y-4">
                {rfqInbox.map((rec) => {
                  const form = meenaQuoteForms[rec.recommendation_id] || {};
                  return (
                    <div key={rec.recommendation_id} className="bg-white rounded-xl border border-slate-200 p-5">
                      {/* Header row */}
                      <div className="flex items-start justify-between mb-4">
                        <div>
                          <p className="font-mono font-semibold text-slate-900 text-sm">{rec.sku_code}</p>
                          <p className="text-slate-500 text-xs mt-0.5">{rec.sku_name}</p>
                        </div>
                        <span className="px-2 py-0.5 rounded text-[11px] font-semibold uppercase tracking-wider bg-blue-50 text-blue-700 ring-1 ring-inset ring-blue-200">
                          RFQ Open
                        </span>
                      </div>

                      {/* Order details */}
                      <div className="grid grid-cols-3 gap-3 bg-slate-50 rounded-lg p-3 mb-4 text-xs">
                        <div>
                          <p className="text-[10px] text-slate-400 uppercase tracking-wide font-medium">Quantity</p>
                          <p className="font-semibold text-slate-800 mt-0.5">
                            {rec.quantity.toLocaleString()} {rec.unit}
                          </p>
                        </div>
                        <div>
                          <p className="text-[10px] text-slate-400 uppercase tracking-wide font-medium">Vendors Asked</p>
                          <p className="font-semibold text-slate-800 mt-0.5">{rec.vendors_contacted}</p>
                        </div>
                        <div>
                          <p className="text-[10px] text-slate-400 uppercase tracking-wide font-medium">Quotes In</p>
                          <p className="font-semibold text-slate-800 mt-0.5">{rec.quotes_received}</p>
                        </div>
                      </div>

                      {/* Quote form */}
                      <div className="border-t border-slate-100 pt-4">
                        <p className="text-[11px] font-semibold text-slate-500 uppercase tracking-wide mb-3">
                          Submit your proforma
                        </p>
                        <div className="flex gap-2 mb-3">
                          <div className="flex-1">
                            <label className="block text-[10px] text-slate-400 mb-1">Unit Price (₹)</label>
                            <input
                              type="number"
                              min="0"
                              step="0.01"
                              placeholder="e.g. 45.00"
                              value={form.price || ""}
                              onChange={(e) =>
                                setMeenaQuoteForms((prev) => ({
                                  ...prev,
                                  [rec.recommendation_id]: { ...prev[rec.recommendation_id], price: e.target.value },
                                }))
                              }
                              className="w-full border border-slate-200 rounded-lg px-3 py-2 text-xs focus:outline-none focus:ring-2 focus:ring-slate-300"
                            />
                          </div>
                          <div className="flex-1">
                            <label className="block text-[10px] text-slate-400 mb-1">Lead Time (days)</label>
                            <input
                              type="number"
                              min="1"
                              step="1"
                              placeholder="e.g. 7"
                              value={form.lead || ""}
                              onChange={(e) =>
                                setMeenaQuoteForms((prev) => ({
                                  ...prev,
                                  [rec.recommendation_id]: { ...prev[rec.recommendation_id], lead: e.target.value },
                                }))
                              }
                              className="w-full border border-slate-200 rounded-lg px-3 py-2 text-xs focus:outline-none focus:ring-2 focus:ring-slate-300"
                            />
                          </div>
                        </div>
                        <button
                          onClick={() => submitMeenaQuote(rec.recommendation_id)}
                          className="w-full py-2 bg-slate-900 hover:bg-slate-700 text-white text-xs font-medium rounded-lg transition-colors"
                        >
                          Submit Quote
                        </button>
                      </div>
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        )}

        {/* ══ MEENA: OPEN ORDERS ════════════════════════════════════════ */}
        {persona === "meena" && activeTab === "Open Orders" && (() => {
          const statusLabel = {
            quotes_received: "Quote Accepted",
            po_issued:       "PO Received",
            invoice_received:"Invoiced",
          };
          const statusStyle = {
            quotes_received: "bg-blue-50 text-blue-700 ring-1 ring-inset ring-blue-200",
            po_issued:       "bg-violet-50 text-violet-700 ring-1 ring-inset ring-violet-200",
            invoice_received:"bg-emerald-50 text-emerald-700 ring-1 ring-inset ring-emerald-200",
          };
          const meenaOrders = orders.filter(
            (o) =>
              o.winning_vendor === "Punjab Components House" &&
              ["quotes_received", "po_issued", "invoice_received"].includes(o.status)
          );

          return (
            <div>
              <SectionHeader title="Open Orders" sub="Orders where you are the selected vendor" />

              {meenaOrders.length === 0 ? (
                <div className="bg-white border border-slate-200 rounded-xl px-6 py-12 text-center">
                  <p className="text-slate-600 font-medium text-sm">No won orders yet</p>
                  <p className="text-slate-400 text-xs mt-1">
                    Orders where your quote wins the AI evaluation will appear here
                  </p>
                </div>
              ) : (
                <div className="space-y-3">
                  {meenaOrders.map((o) => (
                    <div key={o.id} className="bg-white rounded-xl border border-slate-200 p-5">
                      <div className="flex items-start justify-between mb-3">
                        <div>
                          <p className="font-mono font-semibold text-slate-900 text-sm">{o.sku_code}</p>
                          <p className="text-slate-400 text-xs mt-0.5">
                            {o.sku_name} · {o.quantity.toLocaleString()} {o.unit}
                          </p>
                        </div>
                        <span className={`px-2 py-0.5 rounded text-[11px] font-semibold uppercase ${statusStyle[o.status] || ""}` }>
                          {statusLabel[o.status] || o.status}
                        </span>
                      </div>

                      <div className="grid grid-cols-2 gap-3 bg-slate-50 rounded-lg p-3 mb-3">
                        <div>
                          <p className="text-[10px] text-slate-400 uppercase tracking-wide font-medium">Order Value</p>
                          <p className="text-sm font-semibold text-slate-800 mt-0.5">{formatINR(o.order_value)}</p>
                        </div>
                        <div>
                          <p className="text-[10px] text-slate-400 uppercase tracking-wide font-medium">
                            {o.po_number ? "PO Number" : "Ref"}
                          </p>
                          <p className="text-sm font-semibold text-slate-800 mt-0.5">{o.po_number || `#${o.id}`}</p>
                        </div>
                      </div>

                      {o.po_url && (
                        <a
                          href={o.po_url} target="_blank" rel="noreferrer"
                          className="inline-block text-xs font-medium text-blue-600 hover:underline"
                        >
                          View Purchase Order
                        </a>
                      )}
                    </div>
                  ))}
                </div>
              )}
            </div>
          );
        })()}

      </main>

      {/* ── CHAINFLOW COPILOT CHAT BAR (Rohan only) ────────────────────── */}
      {persona === "rohan" && (
        <div className="fixed bottom-0 left-0 right-0 z-40 bg-slate-950/95 backdrop-blur border-t border-slate-800 px-4 py-3">
          <div className="max-w-7xl mx-auto">
            {chatAnswer && (
              <div className="mb-2 bg-slate-800 rounded-lg px-4 py-2.5 text-xs text-slate-200 leading-relaxed max-h-28 overflow-y-auto">
                <span className="text-blue-400 font-semibold mr-2">Copilot:</span>
                {chatAnswer}
              </div>
            )}
            <form
              onSubmit={(e) => { e.preventDefault(); submitChat(); }}
              className="flex gap-2 items-center"
            >
              <div className="flex-1 flex items-center gap-2 bg-slate-800 border border-slate-700 rounded-lg px-3 py-2">
                {chatLoading && <Spinner className="w-3 h-3 border-blue-400 flex-shrink-0" />}
                <input
                  type="text"
                  value={chatQuestion}
                  onChange={(e) => setChatQuestion(e.target.value)}
                  onKeyDown={(e) => e.key === "Enter" && !e.shiftKey && (e.preventDefault(), submitChat())}
                  placeholder="Ask ChainFlow Copilot — e.g. 'Which vendor has the best price for ELAS-YARN?'"
                  className="flex-1 bg-transparent text-xs text-white placeholder-slate-500 focus:outline-none"
                  disabled={chatLoading}
                />
              </div>
              <button
                type="submit"
                disabled={chatLoading || !chatQuestion.trim()}
                className="px-4 py-2 bg-blue-600 hover:bg-blue-500 disabled:bg-slate-700 disabled:text-slate-500 text-white text-xs font-medium rounded-lg transition-colors"
              >
                Ask
              </button>
              {chatAnswer && (
                <button
                  type="button"
                  onClick={() => { setChatAnswer(null); setChatQuestion(""); }}
                  className="px-3 py-2 border border-slate-700 hover:bg-slate-800 text-slate-400 text-xs rounded-lg transition-colors"
                >
                  Clear
                </button>
              )}
            </form>
          </div>
        </div>
      )}

      {/* ── TOAST ──────────────────────────────────────────────────────── */}
      {toast && (
        <div
          className={`fixed bottom-6 right-6 px-4 py-3 rounded-xl text-xs font-medium z-50 shadow-xl border ${
            toast.type === "success"
              ? "bg-slate-900 text-white border-slate-700"
              : "bg-red-600 text-white border-red-500"
          }`}
        >
          {toast.message}
        </div>
      )}
    </div>
  );
}