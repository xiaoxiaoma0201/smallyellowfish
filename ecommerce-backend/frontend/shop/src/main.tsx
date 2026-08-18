import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { ApiError, api } from "../../shared/api";
import type {
  Balance,
  BalanceTransaction,
  AfterSalePolicy,
  Cart,
  CartItem,
  CurrentAccount,
  CustomerServicePageContext,
  CustomerServiceResponse,
  FaqEntry,
  SellerOrder,
  SellerProduct,
  ShopOrder,
  ShopProduct
} from "../../shared/types";
import "./styles.css";

type Route =
  | { name: "home" }
  | { name: "product"; productId: number }
  | { name: "cart" }
  | { name: "checkout" }
  | { name: "pay"; orderNo: string }
  | { name: "orders" }
  | { name: "order"; orderNo: string }
  | { name: "balance" }
  | { name: "policies" }
  | { name: "seller" };

type Toast = { tone: "ok" | "error"; text: string } | null;
type CsMessage = {
  from: "user" | "agent" | "system";
  text: string;
  contextTag?: string;
  response?: CustomerServiceResponse;
};

const money = (value?: number | null) => `¥${Number(value ?? 0).toFixed(2)}`;
const time = (value?: string | null) => (value ? value.replace("T", " ").slice(0, 16) : "-");

const statusText: Record<string, string> = {
  PENDING_PAYMENT: "待支付",
  PAID_PENDING_SHIPMENT: "待发货",
  PENDING_SHIPMENT: "待发货",
  SHIPPED: "已发货",
  DELIVERED: "已签收",
  CANCELED: "已取消",
  PAID: "已支付",
  UNPAID: "未支付",
  REFUNDED: "已退款"
};

function parseRoute(): Route {
  const path = window.location.pathname.replace(/^\/shop/, "") || "/";
  if (path.startsWith("/products/")) return { name: "product", productId: Number(path.split("/")[2]) };
  if (path === "/cart") return { name: "cart" };
  if (path === "/checkout") return { name: "checkout" };
  if (path.startsWith("/pay/")) return { name: "pay", orderNo: decodeURIComponent(path.split("/")[2]) };
  if (path === "/orders") return { name: "orders" };
  if (path.startsWith("/orders/")) return { name: "order", orderNo: decodeURIComponent(path.split("/")[2]) };
  if (path === "/balance") return { name: "balance" };
  if (path === "/policies") return { name: "policies" };
  if (path === "/seller") return { name: "seller" };
  return { name: "home" };
}

function App() {
  const [route, setRoute] = useState<Route>(parseRoute);
  const [account, setAccount] = useState<CurrentAccount | null>(null);
  const [authChecked, setAuthChecked] = useState(false);
  const [toast, setToast] = useState<Toast>(null);
  const [cartCount, setCartCount] = useState(0);
  const [csOpen, setCsOpen] = useState(false);
  const [csContext, setCsContext] = useState<CustomerServicePageContext>({ type: "HOME" });

  const go = useCallback((to: string) => {
    window.history.pushState(null, "", to);
    setRoute(parseRoute());
  }, []);

  const notify = useCallback((tone: "ok" | "error", text: string) => {
    setToast({ tone, text });
    window.setTimeout(() => setToast(null), 2600);
  }, []);

  const handleError = useCallback((error: unknown) => {
    if (error instanceof ApiError) {
      if (error.status === 401) {
        setAccount(null);
        return "当前身份已失效，正在自动重新进入商城";
      }
      if (error.status === 403) return "当前账号无权访问用户商城";
      return error.message;
    }
    return "网络异常，请稍后再试";
  }, []);

  const refreshCartCount = useCallback(async () => {
    if (!account || account.role !== "USER") return;
    try {
      const cart = await api.cart();
      setCartCount(cart.items.reduce((sum, item) => sum + item.quantity, 0));
    } catch {
      setCartCount(0);
    }
  }, [account]);

  useEffect(() => {
    const listener = () => setRoute(parseRoute());
    window.addEventListener("popstate", listener);
    return () => window.removeEventListener("popstate", listener);
  }, []);

  // 测试模式：不再展示登录页。启动时读取当前会话，无会话时自动以默认买家身份进入。
  useEffect(() => {
    api.me()
      .then((me) => {
        if (me.role === "ADMIN") {
          setAccount(null);
        } else {
          setAccount(me);
        }
      })
      .catch(() => setAccount(null))
      .finally(() => setAuthChecked(true));
  }, [go]);

  useEffect(() => {
    if (!authChecked || account) return;
    api.login("zhangsan", "123456")
      .then((me) => {
        if (me.role === "USER") {
          setAccount(me);
        }
      })
      .catch(() => undefined);
  }, [authChecked, account]);

  const switchUser = useCallback(async (username: string) => {
    if (account?.username === username) return;
    try {
      const next = await api.login(username, "123456");
      if (next.role !== "USER") return;
      setAccount(next);
      setCsOpen(false);
      setCsContext({ type: "HOME" });
      notify("ok", `已切换为 ${next.nickname ?? next.username}`);
      go("/");
    } catch {
      notify("error", "切换身份失败，请稍后再试");
    }
  }, [account?.username, go, notify]);

  useEffect(() => {
    void refreshCartCount();
  }, [refreshCartCount]);

  const openService = (context: CustomerServicePageContext) => {
    setCsContext(context);
    setCsOpen(true);
  };

  if (!authChecked) return <ShellSkeleton />;

  return (
    <div className={csOpen && account?.role === "USER" ? "app-shell service-open" : "app-shell"}>
      {account && (
        <Header
          account={account}
          cartCount={cartCount}
          go={go}
          onService={() => openService({ type: "HOME" })}
          onSwitchUser={switchUser}
        />
      )}
      <main className="page">
        {account?.role === "ADMIN" ? (
          <StateBlock title="当前账号无权访问用户商城" text="管理员账号请使用管理后台入口。" />
        ) : (
          <ShopRoutes
            route={route}
            go={go}
            notify={notify}
            handleError={handleError}
            refreshCartCount={refreshCartCount}
            openService={openService}
            account={account}
          />
        )}
      </main>
      {account?.role === "USER" && <ShopFooter go={go} />}
      {toast && <div className={`toast ${toast.tone}`}>{toast.text}</div>}
      {account?.role === "USER" && (
        <CustomerServiceDrawer
          open={csOpen}
          context={csContext}
          onClose={() => setCsOpen(false)}
          onError={handleError}
        />
      )}
    </div>
  );
}

const DEMO_USERS = [
  { username: "zhangsan", label: "张三", desc: "买家 U1001" },
  { username: "lisi", label: "李四", desc: "卖家 U1002" },
  { username: "wangwu", label: "王五", desc: "买家 U1003" }
];

const USER_ROLE: Record<string, string> = {
  U1001: "买家",
  U1002: "卖家",
  U1003: "买家"
};

function Header({ account, cartCount, go, onService, onSwitchUser }: {
  account: CurrentAccount;
  cartCount: number;
  go: (to: string) => void;
  onService: () => void;
  onSwitchUser: (username: string) => void;
}) {
  const [accountOpen, setAccountOpen] = useState(false);
  const [balance, setBalance] = useState<Balance | null>(null);
  const [balanceLoading, setBalanceLoading] = useState(false);
  const [balanceError, setBalanceError] = useState("");

  useEffect(() => {
    if (!accountOpen) return;
    setBalanceLoading(true);
    setBalanceError("");
    api.balance()
      .then(setBalance)
      .catch(() => setBalanceError("余额读取失败"))
      .finally(() => setBalanceLoading(false));
  }, [accountOpen]);

  const openBalance = () => {
    setAccountOpen(false);
    go("/balance");
  };

  const pickUser = (username: string) => {
    setAccountOpen(false);
    onSwitchUser(username);
  };

  return (
    <header className="topbar">
      <button className="brand" onClick={() => go("/")}>
        <span className="brand-mark" aria-label="小黄鱼二手电商交易平台">🐟</span>
        <span><strong>小黄鱼二手电商交易平台</strong><small>闲置好物，循环利用</small></span>
      </button>
      <nav>
        <button onClick={() => go("/")}>商品</button>
        <button onClick={() => go("/cart")}>购物车 {cartCount > 0 && <b>{cartCount}</b>}</button>
        <button onClick={() => go("/orders")}>我的订单</button>
        <button onClick={() => go("/seller")}>卖家中心</button>
        <button onClick={onService}>客服</button>
      </nav>
      <div className="account">
        <button className="account-trigger" onClick={() => setAccountOpen((open) => !open)} aria-expanded={accountOpen}>
          <span>{account.nickname ?? account.username}</span>
          <small className="role-chip" data-role={USER_ROLE[account.userId ?? ""] ?? "买家"}>{USER_ROLE[account.userId ?? ""] ?? "买家"}</small>
        </button>
        {accountOpen && (
          <div className="account-menu">
            <div className="account-card">
              <strong>{account.nickname ?? account.username}</strong>
              <span>身份：{USER_ROLE[account.userId ?? ""] ?? "买家"}</span>
              <span>User ID：{account.userId ?? "-"}</span>
              <span>账号：{account.username}</span>
              <span>手机号：{account.mobile ?? "未绑定"}</span>
            </div>
            <button className="balance-menu-item" onClick={openBalance}>
              <span>账户余额</span>
              <strong>{balanceLoading ? "读取中..." : balanceError || money(balance?.availableBalance)}</strong>
            </button>
            <div className="switch-users">
              <span className="menu-title">切换测试身份</span>
              {DEMO_USERS.map((user) => (
                <button
                  key={user.username}
                  className={account.username === user.username ? "active" : ""}
                  onClick={() => pickUser(user.username)}
                >
                  <b>{user.label}</b>
                  <small>{user.desc}</small>
                </button>
              ))}
            </div>
          </div>
        )}
      </div>
    </header>
  );
}

function ShopRoutes(props: {
  route: Route;
  go: (to: string) => void;
  notify: (tone: "ok" | "error", text: string) => void;
  handleError: (error: unknown) => string;
  refreshCartCount: () => Promise<void>;
  openService: (context: CustomerServicePageContext) => void;
  account: CurrentAccount | null;
}) {
  const { route } = props;
  if (route.name === "product") return <ProductDetailPage {...props} productId={route.productId} />;
  if (route.name === "cart") return <CartPage {...props} />;
  if (route.name === "checkout") return <CheckoutPage {...props} />;
  if (route.name === "pay") return <PayPage {...props} orderNo={route.orderNo} />;
  if (route.name === "orders") return <OrdersPage {...props} />;
  if (route.name === "order") return <OrderDetailPage {...props} orderNo={route.orderNo} />;
  if (route.name === "balance") return <BalancePage {...props} />;
  if (route.name === "policies") return <PoliciesPage handleError={props.handleError} />;
  if (route.name === "seller") return <SellerCenterPage {...props} />;
  return <HomePage {...props} />;
}

function HomePage({ go, notify, handleError, refreshCartCount, openService, account }: PageProps) {
  const [products, setProducts] = useState<ShopProduct[]>([]);
  const [keyword, setKeyword] = useState("");
  const [category, setCategory] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const categories = useMemo(() => Array.from(new Set(products.map((p) => p.category))).filter(Boolean), [products]);
  const promotedProducts = useMemo(() => products.filter((product) => product.promotion), [products]);

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      setProducts(await api.products({ keyword, category }));
    } catch (err) {
      // 首次无会话时的 401 会由自动登录流程恢复，静默等待身份就绪后重载
      setError(err instanceof ApiError && err.status === 401 ? "" : handleError(err));
    } finally {
      setLoading(false);
    }
  }, [account?.username, category, handleError, keyword]);

  useEffect(() => { void load(); }, [load]);

  async function add(product: ShopProduct) {
    try {
      await api.addCartItem(product.productId, 1);
      await refreshCartCount();
      notify("ok", "已加入购物车");
    } catch (err) {
      notify("error", handleError(err));
    }
  }

  return (
    <>
      <section className="shop-hero">
        <div>
          <h1>二手闲置好物，循环焕新生活</h1>
          <p>个人闲置数码、家居、户外好物直购，成色透明、支持验机验货，订单与售后全程可查。</p>
        </div>
        <button onClick={() => openService({ type: "HOME" })}>咨询客服</button>
      </section>
      <CampaignSection products={promotedProducts} selectedCategory={category} setCategory={setCategory} />
      <section className="filters">
        <input placeholder="搜索商品名称或描述" value={keyword} onChange={(e) => setKeyword(e.target.value)} onKeyDown={(e) => e.key === "Enter" && load()} />
        <select value={category} onChange={(e) => setCategory(e.target.value)}>
          <option value="">全部分类</option>
          {categories.map((item) => <option key={item} value={item}>{item}</option>)}
        </select>
        <button onClick={load}>筛选</button>
      </section>
      <DataState loading={loading} error={error} empty={!products.length} emptyText="暂时没有匹配商品。">
        <div className="product-grid">
          {products.map((product) => (
            <article className="product-card" key={product.productId}>
              <ProductArt product={product} />
              <div className="card-body">
                <span className="pill">{product.category}</span>
                <h3>{product.name}</h3>
                <p>{product.description}</p>
                {product.promotion && <PromotionBadge product={product} />}
                <strong>{money(product.price)}</strong>
                <div className="meta-line">
                  <span>{product.supportsSevenDayReturn ? "支持 7 天无理由" : "二手售后按规则执行"}</span>
                  <span>{product.stockQuantity > 0 ? `库存 ${product.stockQuantity} 件` : "已出闲置"}</span>
                </div>
                <div className="actions">
                  <button onClick={() => go(`/products/${product.productId}`)}>查看详情</button>
                  <button className="primary" disabled={!product.purchaseAvailable} onClick={() => add(product)}>
                    {product.purchaseAvailable ? "加入购物车" : "暂不可买"}
                  </button>
                </div>
              </div>
            </article>
          ))}
        </div>
      </DataState>
    </>
  );
}

function CampaignSection({ products, selectedCategory, setCategory }: {
  products: ShopProduct[];
  selectedCategory: string;
  setCategory: (category: string) => void;
}) {
  const campaignVenues = useMemo(() => {
    const group = new Map<string, ShopProduct[]>();
    products.forEach((product) => {
      if (!product.promotion) return;
      const list = group.get(product.promotion.promotionName) ?? [];
      list.push(product);
      group.set(product.promotion.promotionName, list);
    });
    return Array.from(group.entries()).map(([promotionName, items]) => ({
      key: promotionName,
      promotionName,
      count: items.length,
      lowestPrice: Math.min(...items.map((item) => item.promotion?.promotionPrice ?? item.price)),
      title: promotionName,
      description: items[0]?.promotion?.discountSummary ?? "活动价和适用条件会在商品卡片、详情页和结算页同步展示。",
      primaryCategory: items[0]?.category ?? "",
      categories: Array.from(new Set(items.map((item) => item.category)))
    })).sort((left, right) => right.count - left.count || left.promotionName.localeCompare(right.promotionName, "zh-Hans-CN"));
  }, [products]);
  const campaignSlides = useMemo(() => {
    const featuredVenues = campaignVenues.slice(0, 5);
    return [
      {
        key: "all",
        label: "小黄鱼二手好物专场",
        title: "多品类二手专场正在进行",
        description: "数码、家居、家电、个护和户外闲置好物已接入专场价，商品详情页会同步展示当前活动规则。",
        count: products.length,
        category: ""
      },
      ...featuredVenues.map((item) => ({
        key: item.key,
        label: item.title,
        title: `${item.title}正在进行`,
        description: item.description,
        count: item.count,
        category: item.primaryCategory
      }))
    ];
  }, [campaignVenues, products.length]);
  const [activeSlide, setActiveSlide] = useState(0);
  const currentSlide = campaignSlides[activeSlide] ?? campaignSlides[0];

  useEffect(() => {
    if (campaignSlides.length <= 1) return;
    const timer = window.setInterval(() => {
      setActiveSlide((index) => (index + 1) % campaignSlides.length);
    }, 4200);
    return () => window.clearInterval(timer);
  }, [campaignSlides.length]);

  useEffect(() => {
    setActiveSlide(0);
  }, [campaignSlides.length]);

  const switchSlide = (nextIndex: number) => {
    const length = campaignSlides.length || 1;
    setActiveSlide((nextIndex + length) % length);
  };

  return (
    <section className="campaign-panel">
      <div className="campaign-main" aria-live="polite">
        <div className="campaign-copy" key={currentSlide.key}>
          <span>{currentSlide.label}</span>
          <h2>{currentSlide.title}</h2>
          <p>{currentSlide.description}</p>
          <div className="campaign-stats">
            <b>{currentSlide.count}</b><small>款参与活动</small>
          </div>
          <small className="campaign-note">具体可享价格以会员等级、活动条件和结算页为准。</small>
        </div>
        <div className="campaign-controls">
          <button aria-label="上一个活动" onClick={() => switchSlide(activeSlide - 1)}>‹</button>
          <div className="campaign-dots" aria-label="活动轮播">
            {campaignSlides.map((slide, index) => (
              <button
                key={slide.key}
                className={index === activeSlide ? "active" : ""}
                aria-label={`切换到${slide.label}`}
                onClick={() => switchSlide(index)}
              />
            ))}
          </div>
          <button aria-label="下一个活动" onClick={() => switchSlide(activeSlide + 1)}>›</button>
        </div>
      </div>
      <div className="campaign-venues">
        <button className={!selectedCategory ? "active" : ""} onClick={() => setCategory("")}>
          <strong>全部专场</strong>
          <span>查看所有专场好物</span>
        </button>
        {campaignVenues.map((item) => (
          <button
            key={item.key}
            className={selectedCategory && item.categories.includes(selectedCategory) ? "active" : ""}
            onClick={() => setCategory(item.primaryCategory)}
          >
            <strong>{item.title}</strong>
            <span>{item.count} 款，{money(item.lowestPrice)} 起</span>
          </button>
        ))}
      </div>
    </section>
  );
}

function ProductDetailPage({ productId, go, notify, handleError, refreshCartCount, openService }: PageProps & { productId: number }) {
  const [product, setProduct] = useState<ShopProduct | null>(null);
  const [quantity, setQuantity] = useState(1);
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    setLoading(true);
    api.product(productId)
      .then(setProduct)
      .catch((err) => setError(handleError(err)))
      .finally(() => setLoading(false));
  }, [handleError, productId]);

  async function addCart() {
    if (!product) return;
    setSubmitting(true);
    try {
      await api.addCartItem(product.productId, quantity);
      await refreshCartCount();
      notify("ok", "已加入购物车");
    } catch (err) {
      notify("error", handleError(err));
    } finally {
      setSubmitting(false);
    }
  }

  async function buyNow() {
    if (!product) return;
    setSubmitting(true);
    try {
      const order = await api.createOrder({ source: "DIRECT_BUY", productId, quantity });
      go(`/pay/${encodeURIComponent(order.orderNo)}`);
    } catch (err) {
      notify("error", handleError(err));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <DataState loading={loading} error={error} empty={!product} emptyText="商品不存在或已下架。">
      {product && (
        <section className="detail-layout">
          <ProductArt product={product} size="large" />
          <div className="detail-info">
            <span className="pill">{product.category}</span>
            <h1>{product.name}</h1>
            <p>{product.description}</p>
            {product.promotion && <PromotionPanel product={product} />}
            <strong className="price">{money(product.price)}</strong>
            <div className="info-grid">
              <span>库存</span><b>{product.stockQuantity}</b>
              <span>售后</span><b>{product.afterSaleNote ?? "按小黄鱼二手电商交易平台售后规则处理"}</b>
            </div>
            <label className="qty">数量<input type="number" min={1} max={Math.max(product.stockQuantity, 1)} value={quantity} onChange={(e) => setQuantity(Number(e.target.value))} /></label>
            <div className="actions">
              <button onClick={() => openService({ type: "PRODUCT_DETAIL", productId })}>咨询这件商品</button>
              <button disabled={!product.purchaseAvailable || submitting} onClick={addCart}>加入购物车</button>
              <button className="primary" disabled={!product.purchaseAvailable || submitting} onClick={buyNow}>立即购买</button>
            </div>
          </div>
        </section>
      )}
    </DataState>
  );
}

function PromotionBadge({ product }: { product: ShopProduct }) {
  if (!product.promotion) return null;
  return (
    <div className="promotion-badge">
      <span>{product.promotion.promotionName}</span>
      <b>{money(product.promotion.promotionPrice)}</b>
    </div>
  );
}

function PromotionPanel({ product }: { product: ShopProduct }) {
  if (!product.promotion) return null;
  return (
    <section className="promotion-panel">
      <div>
        <span>二手专场</span>
        <h2>{product.promotion.promotionName}</h2>
      </div>
      <strong>{money(product.promotion.promotionPrice)}</strong>
      <p>{product.promotion.discountSummary}</p>
      <small>{time(product.promotion.startAt)} 至 {time(product.promotion.endAt)}</small>
    </section>
  );
}

function CartPage({ go, notify, handleError, refreshCartCount, openService }: PageProps) {
  const [cart, setCart] = useState<Cart | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [busyId, setBusyId] = useState<number | null>(null);
  const [quantityDrafts, setQuantityDrafts] = useState<Record<number, string>>({});

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      setCart(await api.cart());
    } catch (err) {
      setError(handleError(err));
    } finally {
      setLoading(false);
    }
  }, [handleError]);
  useEffect(() => { void load(); }, [load]);
  useEffect(() => {
    if (!cart) return;
    setQuantityDrafts(Object.fromEntries(cart.items.map((item) => [item.itemId, `${item.quantity}`])));
  }, [cart]);

  async function update(item: CartItem, body: { quantity?: number; selected?: boolean }) {
    setBusyId(item.itemId);
    try {
      setCart(await api.updateCartItem(item.itemId, body));
      await refreshCartCount();
    } catch (err) {
      notify("error", handleError(err));
    } finally {
      setBusyId(null);
    }
  }

  async function remove(item: CartItem) {
    setBusyId(item.itemId);
    try {
      await api.deleteCartItem(item.itemId);
      await load();
      await refreshCartCount();
      notify("ok", "已从购物车移除");
    } catch (err) {
      notify("error", handleError(err));
    } finally {
      setBusyId(null);
    }
  }

  function changeQuantityDraft(item: CartItem, value: string) {
    if (/^\d*$/.test(value)) {
      setQuantityDrafts((drafts) => ({ ...drafts, [item.itemId]: value }));
    }
  }

  async function commitQuantity(item: CartItem) {
    const next = Number(quantityDrafts[item.itemId]);
    if (!Number.isInteger(next) || next < 1) {
      setQuantityDrafts((drafts) => ({ ...drafts, [item.itemId]: `${item.quantity}` }));
      notify("error", "商品数量至少为 1");
      return;
    }
    if (next === item.quantity) return;
    await update(item, { quantity: next });
  }

  async function stepQuantity(item: CartItem, delta: number) {
    const next = Math.max(1, item.quantity + delta);
    if (next === item.quantity) return;
    setQuantityDrafts((drafts) => ({ ...drafts, [item.itemId]: `${next}` }));
    await update(item, { quantity: next });
  }

  const canCheckout = !!cart?.items.some((item) => item.selected && item.settlementAvailable);
  return (
    <DataState loading={loading} error={error} empty={!cart?.items.length} emptyText="购物车还是空的，去挑选小黄鱼二手电商交易平台的商品吧。">
      {cart && (
        <section className="two-col">
          <div className="list-panel">
            <h1>购物车</h1>
            {cart.items.map((item) => (
              <div className="cart-row" key={item.itemId}>
                <input type="checkbox" checked={item.selected} disabled={busyId === item.itemId || !item.settlementAvailable} onChange={(e) => update(item, { selected: e.target.checked })} />
                <button className="cart-product-link" onClick={() => go(`/products/${item.productId}`)} aria-label={`查看${item.productName}详情`}>
                  <ProductThumb name={item.productName} imageUrl={item.productImageUrl} />
                  <span>
                    <h3>{item.productName}</h3>
                    <p>{item.unavailableReason ?? `库存 ${item.stockQuantity}`}</p>
                    <CartPrice item={item} />
                  </span>
                </button>
                <div className="quantity-control">
                  <button disabled={busyId === item.itemId || item.quantity <= 1} onClick={() => stepQuantity(item, -1)} aria-label={`减少${item.productName}数量`}>−</button>
                  <input
                    className="small-input"
                    inputMode="numeric"
                    value={quantityDrafts[item.itemId] ?? `${item.quantity}`}
                    disabled={busyId === item.itemId}
                    onChange={(e) => changeQuantityDraft(item, e.target.value)}
                    onBlur={() => commitQuantity(item)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter") {
                        e.currentTarget.blur();
                      }
                    }}
                  />
                  <button disabled={busyId === item.itemId || item.quantity >= item.stockQuantity} onClick={() => stepQuantity(item, 1)} aria-label={`增加${item.productName}数量`}>+</button>
                </div>
                <b>{money(item.unitPrice * item.quantity)}</b>
                <button disabled={busyId === item.itemId} onClick={() => remove(item)}>删除</button>
              </div>
            ))}
          </div>
          <aside className="summary">
            <h2>结算</h2>
            <p>已选 {cart.selectedItemCount} 件</p>
            <strong>{money(cart.selectedTotalAmount)}</strong>
            <button className="primary" disabled={!canCheckout} onClick={() => go("/checkout")}>去确认订单</button>
            <button onClick={() => openService({ type: "CART" })}>购物车咨询</button>
          </aside>
        </section>
      )}
    </DataState>
  );
}

function CartPrice({ item }: { item: CartItem }) {
  const hasPromotion = !!item.promotionName && item.promotionPrice != null;
  const discounted = hasPromotion && item.promotionApplied && item.unitPrice < item.originalUnitPrice;
  return (
    <div className="cart-price">
      {discounted ? (
        <div className="price-pair">
          <span>{money(item.originalUnitPrice)}</span>
          <strong>{money(item.unitPrice)}</strong>
        </div>
      ) : (
        <strong>{money(item.unitPrice)}</strong>
      )}
      {hasPromotion && (
        <small className={item.promotionApplied ? "promo-ok" : "promo-locked"}>
          {item.promotionName}：{item.promotionCondition}
        </small>
      )}
    </div>
  );
}

function CheckoutPage({ go, account, notify, handleError, refreshCartCount }: PageProps) {
  const [cart, setCart] = useState<Cart | null>(null);
  const [remark, setRemark] = useState("");
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    api.cart().then(setCart).catch((err) => setError(handleError(err))).finally(() => setLoading(false));
  }, [handleError]);

  const selected = cart?.items.filter((item) => item.selected && item.settlementAvailable) ?? [];
  async function submit() {
    setSubmitting(true);
    try {
      const order = await api.createOrder({ source: "CART", cartItemIds: selected.map((item) => item.itemId), remark });
      await refreshCartCount();
      go(`/pay/${encodeURIComponent(order.orderNo)}`);
    } catch (err) {
      notify("error", handleError(err));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <DataState loading={loading} error={error} empty={!selected.length} emptyText="没有可结算商品，请先在购物车勾选可售商品。">
      <section className="two-col">
        <div className="list-panel">
          <h1>确认订单</h1>
          {selected.map((item) => <OrderLine key={item.itemId} name={item.productName} quantity={item.quantity} amount={item.unitPrice * item.quantity} />)}
          <label>订单备注<textarea value={remark} onChange={(e) => setRemark(e.target.value)} placeholder="给小黄鱼二手电商交易平台仓配团队的备注" /></label>
        </div>
        <aside className="summary">
          <h2>收货账号</h2>
          <p>{account?.nickname} / {account?.mobile}</p>
          <strong>{money(cart?.selectedTotalAmount)}</strong>
          <button className="primary" disabled={submitting} onClick={submit}>{submitting ? "提交中..." : "提交订单"}</button>
        </aside>
      </section>
    </DataState>
  );
}

function PayPage({ orderNo, go, notify, handleError }: PageProps & { orderNo: string }) {
  const [order, setOrder] = useState<ShopOrder | null>(null);
  const [balance, setBalance] = useState<Balance | null>(null);
  const [loading, setLoading] = useState(true);
  const [paying, setPaying] = useState(false);
  const [error, setError] = useState("");
  const enough = (balance?.availableBalance ?? 0) >= (order?.totalAmount ?? Infinity);
  useEffect(() => {
    Promise.all([api.order(orderNo), api.balance()])
      .then(([nextOrder, nextBalance]) => { setOrder(nextOrder); setBalance(nextBalance); })
      .catch((err) => setError(handleError(err)))
      .finally(() => setLoading(false));
  }, [handleError, orderNo]);

  async function pay() {
    setPaying(true);
    try {
      await api.pay(orderNo);
      notify("ok", "支付成功");
      go(`/orders/${encodeURIComponent(orderNo)}`);
    } catch (err) {
      notify("error", handleError(err));
    } finally {
      setPaying(false);
    }
  }

  return (
    <DataState loading={loading} error={error} empty={!order || !balance} emptyText="订单或余额信息不可用。">
      {order && balance && (
        <section className="payment-panel">
          <h1>模拟余额支付</h1>
          <p>这是小黄鱼二手电商交易平台的模拟余额支付，不是真实支付平台扣款。</p>
          <div className="pay-box">
            <span>订单号</span><b>{order.orderNo}</b>
            <span>订单金额</span><b>{money(order.totalAmount)}</b>
            <span>当前余额</span><b>{money(balance.availableBalance)}</b>
            <span>支付状态</span><b>{statusText[order.paymentStatus] ?? order.paymentStatus}</b>
          </div>
          {!enough && <p className="error-text">余额不足，无法完成支付。</p>}
          <button className="primary" disabled={!enough || paying || order.paymentStatus === "PAID"} onClick={pay}>{paying ? "支付中..." : "确认支付"}</button>
        </section>
      )}
    </DataState>
  );
}

function OrdersPage({ go, handleError, openService }: PageProps) {
  const [orders, setOrders] = useState<ShopOrder[]>([]);
  const [status, setStatus] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try { setOrders(await api.orders(status)); } catch (err) { setError(handleError(err)); } finally { setLoading(false); }
  }, [handleError, status]);
  useEffect(() => { void load(); }, [load]);
  return (
    <>
      <section className="section-head">
        <h1>我的订单</h1>
        <select value={status} onChange={(e) => setStatus(e.target.value)}>
          <option value="">全部状态</option>
          <option value="PENDING_PAYMENT">待支付</option>
          <option value="PENDING_SHIPMENT">待发货</option>
          <option value="SHIPPED">已发货</option>
          <option value="DELIVERED">已签收</option>
        </select>
      </section>
      <DataState loading={loading} error={error} empty={!orders.length} emptyText="还没有订单。">
        <div className="order-list">
          {orders.map((order) => (
            <article className="order-card" key={order.orderNo}>
              <div><h3>{order.itemSummary}</h3><p>{order.orderNo} · {time(order.createdAt)}</p></div>
              <strong>{money(order.totalAmount)}</strong>
              <span className="pill">{statusText[order.orderStatus] ?? order.orderStatus}</span>
              <div className="actions">
                {order.orderStatus === "PENDING_PAYMENT" && <button onClick={() => go(`/pay/${order.orderNo}`)}>去支付</button>}
                <button onClick={() => openService({ type: "ORDER_DETAIL", orderNo: order.orderNo })}>咨询</button>
                <button className="primary" onClick={() => go(`/orders/${order.orderNo}`)}>详情</button>
              </div>
            </article>
          ))}
        </div>
      </DataState>
    </>
  );
}

function OrderDetailPage({ orderNo, go, handleError, openService }: PageProps & { orderNo: string }) {
  const [order, setOrder] = useState<ShopOrder | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  useEffect(() => {
    api.order(orderNo).then(setOrder).catch((err) => setError(handleError(err))).finally(() => setLoading(false));
  }, [handleError, orderNo]);
  return (
    <DataState loading={loading} error={error} empty={!order} emptyText="订单不存在或无权访问。">
      {order && (
        <section className="detail-stack">
          <div className="section-head">
            <div><h1>订单详情</h1><p>{order.orderNo}</p></div>
            <div className="actions">
              {order.orderStatus === "PENDING_PAYMENT" && <button onClick={() => go(`/pay/${order.orderNo}`)}>继续支付</button>}
              <button className="primary" onClick={() => openService({ type: "ORDER_DETAIL", orderNo: order.orderNo })}>联系小黄鱼客服</button>
            </div>
          </div>
          <div className="info-grid wide">
            <span>订单状态</span><b>{statusText[order.orderStatus] ?? order.orderStatus}</b>
            <span>支付状态</span><b>{statusText[order.paymentStatus] ?? order.paymentStatus}</b>
            <span>履约状态</span><b>{order.fulfillmentStatus ?? "-"}</b>
            <span>物流单号</span><b>{order.logisticsNo ?? "暂无物流单号"}</b>
            <span>下单时间</span><b>{time(order.createdAt)}</b>
            <span>支付时间</span><b>{time(order.paidAt)}</b>
          </div>
          <div className="list-panel">
            <h2>商品明细</h2>
            {order.items.map((item) => <OrderLine key={`${item.productId}-${item.productName}`} name={item.productName} quantity={item.quantity} amount={item.unitPrice * item.quantity} />)}
          </div>
          <aside className="summary inline">
            <span>订单总额</span><strong>{money(order.totalAmount)}</strong>
            <p>{order.afterSaleAvailable ? `可咨询售后：${order.availableAfterSaleTypes.join(" / ")}` : "当前订单暂无可发起售后类型"}</p>
          </aside>
        </section>
      )}
    </DataState>
  );
}

function BalancePage({ handleError }: PageProps) {
  const [balance, setBalance] = useState<Balance | null>(null);
  const [items, setItems] = useState<BalanceTransaction[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  useEffect(() => {
    Promise.all([api.balance(), api.balanceTransactions()])
      .then(([nextBalance, nextItems]) => { setBalance(nextBalance); setItems(nextItems); })
      .catch((err) => setError(handleError(err)))
      .finally(() => setLoading(false));
  }, [handleError]);
  return (
    <DataState loading={loading} error={error} empty={!balance} emptyText="余额账户不可用。">
      {balance && (
        <section className="detail-stack">
          <div className="balance-hero">
            <div><span>模拟账户余额</span><strong>{money(balance.availableBalance)}</strong><p>演示账户余额不代表真实资金账户。</p></div>
          </div>
          <div className="list-panel">
            <h2>余额流水</h2>
            {!items.length ? <StateBlock title="暂无流水" text="产生支付或售后退款后会展示在这里。" /> : items.map((item) => (
              <div className="transaction-row" key={item.transactionNo}>
                <div><b>{item.type}</b><p>{item.remark ?? item.transactionNo}</p></div>
                <strong className={item.amount >= 0 ? "income" : "expense"}>{money(item.amount)}</strong>
                <span>{money(item.balanceBefore)} &gt; {money(item.balanceAfter)}</span>
                <time>{time(item.createdAt)}</time>
              </div>
            ))}
          </div>
        </section>
      )}
    </DataState>
  );
}

function PoliciesPage({ handleError }: { handleError: (error: unknown) => string }) {
  const [policies, setPolicies] = useState<AfterSalePolicy[]>([]);
  const [faqs, setFaqs] = useState<FaqEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    Promise.all([api.afterSalePolicies(), api.faqs()])
      .then(([nextPolicies, nextFaqs]) => { setPolicies(nextPolicies); setFaqs(nextFaqs); })
      .catch((err) => setError(handleError(err)))
      .finally(() => setLoading(false));
  }, [handleError]);

  return (
    <DataState loading={loading} error={error} empty={!policies.length && !faqs.length} emptyText="暂时没有政策内容。">
      <section className="policy-page">
        <div className="section-head">
          <div>
            <h1>小黄鱼二手电商交易平台服务政策</h1>
            <p>退款、退货、换货、发票、配送和活动优惠说明。</p>
          </div>
        </div>
        <div className="policy-grid">
          {policies.map((policy) => (
            <article className="policy-card" key={policy.sceneKey}>
              <h2>{policy.title}</h2>
              <p>{policy.content}</p>
              <dl>
                <dt>适用条件</dt><dd>{policy.applicableConditions || policy.eligibility}</dd>
                <dt>不适用</dt><dd>{policy.exclusionConditions || "-"}</dd>
                <dt>所需凭证</dt><dd>{policy.requiredEvidence || "-"}</dd>
              </dl>
            </article>
          ))}
        </div>
        <div className="list-panel">
          <h2>常见问题</h2>
          {faqs.map((faq) => (
            <div className="faq-row" key={`${faq.category}-${faq.question}`}>
              <span>{faq.category}</span>
              <b>{faq.question}</b>
              <p>{faq.answer}</p>
            </div>
          ))}
        </div>
      </section>
    </DataState>
  );
}

function CustomerServiceDrawer({ open, context, onClose, onError }: {
  open: boolean;
  context: CustomerServicePageContext;
  onClose: () => void;
  onError: (error: unknown) => string;
}) {
  const [messages, setMessages] = useState<CsMessage[]>([
    { from: "agent", text: "您好，我是小黄鱼二手电商交易平台的客服助手，请问有什么可以帮您？" }
  ]);
  const [input, setInput] = useState("");
  const [sessionId, setSessionId] = useState<string | undefined>();
  const [loading, setLoading] = useState(false);
  const [copied, setCopied] = useState(false);
  const busyRef = useRef(false);
  if (!open) return null;

  async function copySessionId() {
    if (!sessionId) return;
    try {
      await navigator.clipboard.writeText(sessionId);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1600);
    } catch {
      setCopied(false);
    }
  }

  function contextTagForMessage(pageContext: CustomerServicePageContext) {
    if (pageContext.type === "PRODUCT_DETAIL") return "已附带商品信息";
    if (pageContext.type === "ORDER_DETAIL") return pageContext.orderNo ? `已附带订单信息：${pageContext.orderNo}` : "已附带订单信息";
    if (pageContext.type === "CART") return "已附带购物车信息";
    return undefined;
  }

  async function send() {
    const text = input.trim();
    if (busyRef.current || !text) return;
    busyRef.current = true;
    setInput("");
    setLoading(true);
    setMessages((prev) => [...prev, { from: "user", text, contextTag: contextTagForMessage(context) }]);
    try {
      const response = await api.chat({ message: text, sessionId, pageContext: context });
      setSessionId(response.sessionId);
      setMessages((prev) => [...prev, { from: "agent", text: response.answer, response }]);
    } catch (err) {
      setMessages((prev) => [...prev, { from: "system", text: onError(err) || "客服繁忙，请稍后再试。" }]);
    } finally {
      busyRef.current = false;
      setLoading(false);
    }
  }

  async function decide(response: CustomerServiceResponse, decision: "APPROVED" | "REJECTED") {
    if (busyRef.current) return;
    if (decision === "REJECTED") {
      setMessages((prev) => [...prev, { from: "system", text: "确认您暂时不打算提交申请，小黄鱼二手电商交易平台客服不会继续执行该操作。" }]);
      return;
    }
    if (!response.resumeToken) return;
    busyRef.current = true;
    setLoading(true);
    try {
      const next = await api.resume({
        sessionId: response.sessionId,
        resumeToken: response.resumeToken,
        decision,
        relatedOrderNo: response.relatedOrderNo,
        relatedAfterSaleNo: response.relatedAfterSaleNo
      });
      setSessionId(next.sessionId);
      setMessages((prev) => [...prev, { from: "agent", text: next.answer, response: next }]);
    } catch (err) {
      setMessages((prev) => [...prev, { from: "system", text: onError(err) || "客服繁忙，请稍后再试。" }]);
    } finally {
      busyRef.current = false;
      setLoading(false);
    }
  }

  return (
    <aside className="service-drawer">
      <div className="drawer-head">
        <div>
          <b>小黄鱼客服</b>
          <span>
            {context.type}
            {sessionId ? (
              <>
                {" · "}会话 {sessionId}
                <button className="copy-session" onClick={copySessionId} title="复制会话 ID 到调试台联动面板">
                  {copied ? "已复制" : "复制"}
                </button>
              </>
            ) : null}
          </span>
        </div>
        <button onClick={onClose}>关闭</button>
      </div>
      <div className="messages">
        {messages.map((message, index) => (
          <div className={`message ${message.from}`} key={index}>
            <p>{message.text}</p>
            {message.contextTag && <span className="message-context-tag">{message.contextTag}</span>}
            {message.response?.transferredToHuman && (
              <span className="message-human-badge" title="Agent 已把本次问题转交人工客服处理">已转人工</span>
            )}
            {message.response?.confirmRequired && (
              <div className="confirm-card">
                <b>{message.response.confirmationTitle ?? "需要确认"}</b>
                <span>{message.response.confirmationSummary}</span>
                <button className="primary" disabled={loading} onClick={() => decide(message.response!, "APPROVED")}>确认提交申请</button>
                <button disabled={loading} onClick={() => decide(message.response!, "REJECTED")}>暂不提交</button>
              </div>
            )}
          </div>
        ))}
        {loading && <div className="message system"><p>小黄鱼客服正在处理，请稍候...</p></div>}
      </div>
      <div className="drawer-input">
        <input
          value={input}
          disabled={loading}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              void send();
            }
          }}
          placeholder={loading ? "小黄鱼客服正在回复，请稍候" : "请输入您的问题"}
        />
        <button className="primary" disabled={loading} onClick={send}>{loading ? "等待中" : "发送"}</button>
      </div>
    </aside>
  );
}

type PageProps = {
  go: (to: string) => void;
  notify: (tone: "ok" | "error", text: string) => void;
  handleError: (error: unknown) => string;
  refreshCartCount: () => Promise<void>;
  openService: (context: CustomerServicePageContext) => void;
  account: CurrentAccount | null;
};

const SELLER_STATUS_TEXT: Record<string, string> = {
  PENDING_REVIEW: "待审核",
  ON_SALE: "在售",
  SOLD: "已售出"
};

function SellerCenterPage({ go, notify, handleError, account }: PageProps) {
  const [tab, setTab] = useState<"products" | "orders">("products");
  const [products, setProducts] = useState<SellerProduct[]>([]);
  const [orders, setOrders] = useState<SellerOrder[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [publishOpen, setPublishOpen] = useState(false);

  const loadProducts = useCallback(async () => {
    if (!account) return;
    setLoading(true);
    setError("");
    try {
      setProducts(await api.sellerProducts(account.userId ?? ""));
    } catch (err) {
      setError(handleError(err));
    } finally {
      setLoading(false);
    }
  }, [account, handleError]);

  const loadOrders = useCallback(async () => {
    if (!account) return;
    setLoading(true);
    setError("");
    try {
      setOrders(await api.sellerOrders(account.userId ?? ""));
    } catch (err) {
      setError(handleError(err));
    } finally {
      setLoading(false);
    }
  }, [account, handleError]);

  useEffect(() => {
    if (account?.userId && USER_ROLE[account.userId] === "卖家") {
      void loadProducts();
    }
  }, [account?.userId, loadProducts]);

  useEffect(() => {
    if (tab === "orders" && account?.userId && USER_ROLE[account.userId] === "卖家") {
      void loadOrders();
    }
  }, [tab, account?.userId, loadOrders]);

  const ship = async (orderNo: string) => {
    try {
      await api.shipOrder(orderNo);
      notify("ok", `订单 ${orderNo} 已发货`);
      void loadOrders();
    } catch (err) {
      notify("error", handleError(err));
    }
  };

  if (!account) return null;
  if (USER_ROLE[account.userId ?? ""] !== "卖家") {
    return (
      <section className="state-block empty">
        <h2>当前身份不是卖家</h2>
        <p>卖家中心仅对卖家身份开放，请通过右上角账号菜单切换到卖家身份（李四 U1002）后查看。</p>
      </section>
    );
  }

  return (
    <>
      <div className="seller-head">
        <div>
          <h1>卖家中心</h1>
          <p>管理你发布的二手闲置商品，跟进买家订单的履约进度。</p>
        </div>
        <button className="primary" onClick={() => setPublishOpen(true)}>+ 上架新商品</button>
      </div>
      <nav className="tabs">
        <button className={tab === "products" ? "active" : ""} onClick={() => setTab("products")}>我的宝贝</button>
        <button className={tab === "orders" ? "active" : ""} onClick={() => setTab("orders")}>卖出订单</button>
      </nav>
      <DataState loading={loading} error={error} empty={false} emptyText="">
        {tab === "products" ? (
          products.length === 0 ? (
            <section className="state-block empty">
              <h2>还没有发布商品</h2>
              <p>点击右上角「上架新商品」发布你的第一件闲置。</p>
            </section>
          ) : (
            <div className="seller-list">
              {products.map((product) => (
                <div className="seller-item" key={product.productId}>
                  <div className="seller-item-main">
                    <b>{product.name}</b>
                    <small>{product.category} · 标价 {money(product.price)} · 库存 {product.stockQuantity}</small>
                    {product.saleStatus === "SOLD" && (
                      <small className="sold-note">买家 {product.buyerUserId ?? "未知"} · 售出于 {time(product.soldAt)} · 订单 {product.soldOrderNo ?? "-"}</small>
                    )}
                  </div>
                  <span className={`pill status-${product.saleStatus?.toLowerCase() ?? "unknown"}`}>
                    {SELLER_STATUS_TEXT[product.saleStatus ?? ""] ?? product.saleStatus}
                  </span>
                </div>
              ))}
            </div>
          )
        ) : orders.length === 0 ? (
          <section className="state-block empty">
            <h2>还没有卖出订单</h2>
            <p>买家购买你的商品并支付后，这里会实时显示订单并支持发货。</p>
          </section>
        ) : (
          <div className="seller-list">
            {orders.map((order) => (
              <div className="seller-item" key={order.orderNo}>
                <div className="seller-item-main">
                  <b>{order.itemSummary}</b>
                  <small>买家 {order.buyerName ?? order.buyerUserId} · {money(order.totalAmount)} · {time(order.createdAt)}</small>
                  {order.logisticsNo && <small>物流单号 {order.logisticsNo}</small>}
                </div>
                <span className={`pill status-${order.orderStatus}`}>{statusText[order.orderStatus] ?? order.orderStatus}</span>
                {order.canShip && <button className="primary small" onClick={() => ship(order.orderNo)}>发货</button>}
              </div>
            ))}
          </div>
        )}
      </DataState>
      {publishOpen && <PublishProductForm onClose={() => setPublishOpen(false)} onDone={() => { setPublishOpen(false); void loadProducts(); }} handleError={handleError} notify={notify} />}
    </>
  );
}

function PublishProductForm({ onClose, onDone, handleError, notify }: {
  onClose: () => void;
  onDone: () => void;
  handleError: (error: unknown) => string;
  notify: (tone: "ok" | "error", text: string) => void;
}) {
  const [name, setName] = useState("");
  const [category, setCategory] = useState("二手闲置");
  const [description, setDescription] = useState("");
  const [price, setPrice] = useState("");
  const [stock, setStock] = useState("1");
  const [submitting, setSubmitting] = useState(false);

  const submit = async () => {
    if (!name.trim()) {
      notify("error", "请填写商品名称");
      return;
    }
    const priceValue = Number(price);
    const stockValue = Number(stock);
    if (!Number.isFinite(priceValue) || priceValue <= 0) {
      notify("error", "请填写正确的价格");
      return;
    }
    if (!Number.isFinite(stockValue) || stockValue < 1) {
      notify("error", "库存至少为 1");
      return;
    }
    setSubmitting(true);
    try {
      await api.createSellerProduct({
        name: name.trim(),
        category: category.trim(),
        description: description.trim(),
        price: priceValue,
        stock: stockValue
      });
      notify("ok", "商品已上架，买家可在商城购买");
      onDone();
    } catch (err) {
      notify("error", handleError(err));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="modal-mask" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <h2>上架二手闲置</h2>
        <label>商品名称<input value={name} onChange={(e) => setName(e.target.value)} placeholder="如：9成新 华为 Mate60（个人闲置）" /></label>
        <label>分类
          <select value={category} onChange={(e) => setCategory(e.target.value)}>
            {["二手闲置", "数码闲置", "家居闲置", "家电闲置", "个护闲置", "户外闲置"].map((item) => <option key={item} value={item}>{item}</option>)}
          </select>
        </label>
        <label>商品描述<textarea value={description} onChange={(e) => setDescription(e.target.value)} placeholder="成色、使用情况、入手渠道等，越详细越容易卖出" /></label>
        <div className="form-row">
          <label>价格（元）<input type="number" min="0.01" step="0.01" value={price} onChange={(e) => setPrice(e.target.value)} placeholder="如 399" /></label>
          <label>库存<input type="number" min="1" value={stock} onChange={(e) => setStock(e.target.value)} /></label>
        </div>
        <div className="modal-actions">
          <button onClick={onClose}>取消</button>
          <button className="primary" disabled={submitting} onClick={submit}>{submitting ? "上架中..." : "确认上架"}</button>
        </div>
      </div>
    </div>
  );
}

function DataState({ loading, error, empty, emptyText, children }: {
  loading: boolean;
  error: string;
  empty: boolean;
  emptyText: string;
  children: React.ReactNode;
}) {
  if (loading) return <StateBlock title="加载中" text="正在连接小黄鱼二手电商交易平台..." />;
  if (error) return <StateBlock title="请求失败" text={error} />;
  if (empty) return <StateBlock title="暂无数据" text={emptyText} />;
  return <>{children}</>;
}

function StateBlock({ title, text }: { title: string; text: string }) {
  return <section className="state-block"><h2>{title}</h2><p>{text}</p></section>;
}

function ShellSkeleton() {
  return <div className="page"><StateBlock title="加载中" text="正在进入小黄鱼二手电商交易平台..." /></div>;
}

function ShopFooter({ go }: { go: (to: string) => void }) {
  return (
    <footer className="shop-footer">
      <button onClick={() => go("/policies")}>政策解读和FAQ</button>
    </footer>
  );
}

function ProductArt({ product, size }: { product: ShopProduct; size?: "large" }) {
  const className = `product-art ${size ?? ""}`;
  if (product.imageUrl) {
    return (
      <div className={className}>
        <img src={product.imageUrl} alt={product.name} />
      </div>
    );
  }
  return <div className={className}><span>{product.name.slice(0, 2)}</span><small>{product.category}</small></div>;
}

function ProductThumb({ name, imageUrl }: { name: string; imageUrl?: string | null }) {
  if (imageUrl) {
    return <div className="thumb"><img src={imageUrl} alt="" /></div>;
  }
  return <div className="thumb">{name.slice(0, 1)}</div>;
}

function OrderLine({ name, quantity, amount }: { name: string; quantity: number; amount: number }) {
  return <div className="order-line"><span>{name} x{quantity}</span><b>{money(amount)}</b></div>;
}

createRoot(document.getElementById("root")!).render(<App />);
