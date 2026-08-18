package com.smallyellowfish.ecommerce.entity;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.GeneratedValue;
import jakarta.persistence.GenerationType;
import jakarta.persistence.Id;
import java.math.BigDecimal;
import java.time.LocalDateTime;

@Entity
public class Product {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @Column(nullable = false, unique = true)
    private String code;

    @Column(nullable = false)
    private String name;

    private String category;

    @Column(length = 1000)
    private String description;

    private BigDecimal price;

    private Integer stock;

    private String highlights;

    private Boolean active;

    private Boolean returnable;

    private String afterSaleLimit;

    private String scenarioTags;

    private String imageUrl;

    /** 发布该商品的卖家用户 ID；平台自营商品为空。 */
    private String sellerId;

    /** 二手商品销售状态：PENDING_REVIEW=待审核 / ON_SALE=在售 / SOLD=已售出。 */
    private String saleStatus;

    /** 已售出时间。 */
    private LocalDateTime soldAt;

    /** 购买该商品的买家用户 ID。 */
    private String soldToUserId;

    /** 售出订单号。 */
    private String soldOrderNo;

    private LocalDateTime createdAt;

    private LocalDateTime updatedAt;

    protected Product() {
    }

    public Product(String code, String name, String category, String description,
                   BigDecimal price, Integer stock, String highlights) {
        this(code, name, category, description, price, stock, highlights, true, true, "", "");
    }

    public Product(String code, String name, String category, String description,
                   BigDecimal price, Integer stock, String highlights, Boolean active,
                   Boolean returnable, String afterSaleLimit, String scenarioTags) {
        this.code = code;
        this.name = name;
        this.category = category;
        this.description = description;
        this.price = price;
        this.stock = stock;
        this.highlights = highlights;
        this.active = active;
        this.returnable = returnable;
        this.afterSaleLimit = afterSaleLimit;
        this.scenarioTags = scenarioTags;
        this.imageUrl = "";
        this.createdAt = LocalDateTime.now();
        this.updatedAt = this.createdAt;
    }

    public Long getId() {
        return id;
    }

    public String getCode() {
        return code;
    }

    public String getName() {
        return name;
    }

    public String getCategory() {
        return category;
    }

    public String getDescription() {
        return description;
    }

    public BigDecimal getPrice() {
        return price;
    }

    public Integer getStock() {
        return stock;
    }

    public void decreaseStock(int quantity) {
        if (stock == null || stock < quantity) {
            throw new IllegalArgumentException("Stock is not enough for product: " + id);
        }
        this.stock = stock - quantity;
    }

    public String getHighlights() {
        return highlights;
    }

    public Boolean getActive() {
        return active;
    }

    public Boolean getReturnable() {
        return returnable;
    }

    public String getAfterSaleLimit() {
        return afterSaleLimit;
    }

    public String getScenarioTags() {
        return scenarioTags;
    }

    public String getImageUrl() {
        return imageUrl;
    }

    public String getSellerId() {
        return sellerId;
    }

    public String getSaleStatus() {
        return saleStatus == null ? "ON_SALE" : saleStatus;
    }

    public LocalDateTime getSoldAt() {
        return soldAt;
    }

    public String getSoldToUserId() {
        return soldToUserId;
    }

    public String getSoldOrderNo() {
        return soldOrderNo;
    }

    public void setSellerListing(String sellerId, String saleStatus) {
        this.sellerId = sellerId;
        this.saleStatus = saleStatus;
        this.updatedAt = LocalDateTime.now();
    }

    /** 标记商品已售出：下架、清库存并记录买家与售出订单。 */
    public void markSold(String buyerUserId, String orderNo) {
        markSold(buyerUserId, orderNo, LocalDateTime.now());
    }

    /** 标记商品已售出并指定售出时间，保证与售出订单的创建时间一致。 */
    public void markSold(String buyerUserId, String orderNo, LocalDateTime soldAt) {
        this.saleStatus = "SOLD";
        this.soldToUserId = buyerUserId;
        this.soldOrderNo = orderNo;
        this.soldAt = soldAt;
        this.stock = 0;
        this.active = false;
        this.updatedAt = LocalDateTime.now();
    }

    public LocalDateTime getCreatedAt() {
        return createdAt;
    }

    public LocalDateTime getUpdatedAt() {
        return updatedAt;
    }

    public void updateCatalogInfo(String name, String category, String description, BigDecimal price,
                                  Integer stock, String imageUrl, Boolean returnable,
                                  String afterSaleLimit) {
        this.name = name;
        this.category = category;
        this.description = description;
        this.price = price;
        this.stock = stock;
        this.imageUrl = imageUrl;
        this.returnable = returnable;
        this.afterSaleLimit = afterSaleLimit;
        this.updatedAt = LocalDateTime.now();
    }

    public void updateAdminFields(String name, String category, String description, BigDecimal price,
                                  Integer stock, String highlights, Boolean returnable,
                                  String afterSaleLimit, String scenarioTags, String imageUrl) {
        this.name = name;
        this.category = category;
        this.description = description;
        this.price = price;
        this.stock = stock;
        this.highlights = highlights;
        this.returnable = returnable;
        this.afterSaleLimit = afterSaleLimit;
        this.scenarioTags = scenarioTags;
        this.imageUrl = imageUrl;
        this.updatedAt = LocalDateTime.now();
    }

    public void updateStock(Integer stock) {
        this.stock = stock;
        this.updatedAt = LocalDateTime.now();
    }

    public void publish() {
        this.active = true;
        this.updatedAt = LocalDateTime.now();
    }

    public void unpublish() {
        this.active = false;
        this.updatedAt = LocalDateTime.now();
    }
}
