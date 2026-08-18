package com.smallyellowfish.ecommerce.controller;

import com.smallyellowfish.ecommerce.dto.ApiResponse;
import com.smallyellowfish.ecommerce.dto.BalanceResponse;
import com.smallyellowfish.ecommerce.dto.BalanceTransactionResponse;
import com.smallyellowfish.ecommerce.dto.CartItemRequest;
import com.smallyellowfish.ecommerce.dto.CartItemUpdateRequest;
import com.smallyellowfish.ecommerce.dto.CartResponse;
import com.smallyellowfish.ecommerce.dto.CreateOrderRequest;
import com.smallyellowfish.ecommerce.dto.CreateSellerProductRequest;
import com.smallyellowfish.ecommerce.dto.PaymentResponse;
import com.smallyellowfish.ecommerce.dto.ShopAfterSaleCreateRequest;
import com.smallyellowfish.ecommerce.dto.ShopAfterSaleResponse;
import com.smallyellowfish.ecommerce.dto.ShopOrderResponse;
import com.smallyellowfish.ecommerce.dto.ShopProductResponse;
import com.smallyellowfish.ecommerce.dto.SellerOrderResponse;
import com.smallyellowfish.ecommerce.dto.SellerProductResponse;
import com.smallyellowfish.ecommerce.dto.ShipOrderRequest;
import com.smallyellowfish.ecommerce.security.AccountPrincipal;
import com.smallyellowfish.ecommerce.security.AgentServiceAuthenticationFilter;
import com.smallyellowfish.ecommerce.security.RequestIdentityResolver;
import com.smallyellowfish.ecommerce.service.ShopService;
import io.swagger.v3.oas.annotations.Operation;
import io.swagger.v3.oas.annotations.tags.Tag;
import jakarta.validation.Valid;
import java.util.List;
import org.springframework.security.core.Authentication;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PatchMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

@Tag(name = "Shop APIs", description = "小黄鱼二手电商交易平台用户商城商品、购物车、订单、余额和售后接口")
@RestController
@RequestMapping("/api/shop")
public class ShopController {

    private final ShopService shopService;
    private final RequestIdentityResolver requestIdentityResolver;

    public ShopController(ShopService shopService, RequestIdentityResolver requestIdentityResolver) {
        this.shopService = shopService;
        this.requestIdentityResolver = requestIdentityResolver;
    }

    @GetMapping("/products")
    @Operation(summary = "List on-sale products")
    public ApiResponse<List<ShopProductResponse>> listProducts(
        @RequestParam(value = "keyword", required = false) String keyword,
        @RequestParam(value = "category", required = false) String category,
        @RequestParam(value = "page", required = false) Integer page,
        @RequestParam(value = "size", required = false) Integer size) {
        return ApiResponse.success(shopService.listProducts(keyword, category));
    }

    @GetMapping("/products/{productId}")
    @Operation(summary = "Get on-sale product details")
    public ApiResponse<ShopProductResponse> getProduct(@PathVariable Long productId) {
        return ApiResponse.success(shopService.getProduct(productId));
    }

    @GetMapping("/seller/products")
    @Operation(summary = "List seller's products with sale status")
    public ApiResponse<List<SellerProductResponse>> listSellerProducts(
        @RequestParam(value = "sellerId") String sellerId) {
        return ApiResponse.success(shopService.listSellerProducts(sellerId));
    }

    @PostMapping("/seller/products")
    @Operation(summary = "Seller publishes a second-hand product")
    public ApiResponse<SellerProductResponse> createSellerProduct(@AuthenticationPrincipal AccountPrincipal principal,
                                                                  @Valid @RequestBody CreateSellerProductRequest request) {
        return ApiResponse.success(shopService.createSellerProduct(principal.getUserId(), request));
    }

    @GetMapping("/seller/orders")
    @Operation(summary = "List seller's sold orders")
    public ApiResponse<List<SellerOrderResponse>> listSellerOrders(
        @RequestParam(value = "sellerId") String sellerId,
        @RequestParam(value = "status", required = false) String status) {
        return ApiResponse.success(shopService.listSellerOrders(sellerId, status));
    }

    @PostMapping("/seller/orders/{orderNo}/ship")
    @Operation(summary = "Seller ships an order")
    public ApiResponse<SellerOrderResponse> shipOrder(@AuthenticationPrincipal AccountPrincipal principal,
                                                      @PathVariable String orderNo,
                                                      @RequestBody(required = false) ShipOrderRequest request) {
        String logisticsNo = request == null ? null : request.logisticsNo();
        return ApiResponse.success(shopService.shipOrder(principal.getUserId(), orderNo, logisticsNo));
    }

    @GetMapping("/cart")
    @Operation(summary = "Get current user's cart")
    public ApiResponse<CartResponse> getCart(
        @RequestHeader(value = AgentServiceAuthenticationFilter.DELEGATED_USER_HEADER, required = false)
        String delegatedUserId,
        Authentication authentication) {
        String currentUserId = requestIdentityResolver.currentUserId(authentication, delegatedUserId);
        return ApiResponse.success(shopService.getCart(currentUserId));
    }

    @PostMapping("/cart/items")
    @Operation(summary = "Add product to current user's cart")
    public ApiResponse<CartResponse> addCartItem(@AuthenticationPrincipal AccountPrincipal principal,
                                                 @Valid @RequestBody CartItemRequest request) {
        return ApiResponse.success(shopService.addCartItem(principal.getUserId(), request));
    }

    @PatchMapping("/cart/items/{itemId}")
    @Operation(summary = "Update current user's cart item")
    public ApiResponse<CartResponse> updateCartItem(@AuthenticationPrincipal AccountPrincipal principal,
                                                    @PathVariable Long itemId,
                                                    @Valid @RequestBody CartItemUpdateRequest request) {
        return ApiResponse.success(shopService.updateCartItem(principal.getUserId(), itemId, request));
    }

    @DeleteMapping("/cart/items/{itemId}")
    @Operation(summary = "Delete current user's cart item")
    public ApiResponse<Void> deleteCartItem(@AuthenticationPrincipal AccountPrincipal principal,
                                            @PathVariable Long itemId) {
        shopService.deleteCartItem(principal.getUserId(), itemId);
        return ApiResponse.success(null);
    }

    @PostMapping("/orders")
    @Operation(summary = "Create current user's order")
    public ApiResponse<ShopOrderResponse> createOrder(@AuthenticationPrincipal AccountPrincipal principal,
                                                      @Valid @RequestBody CreateOrderRequest request) {
        return ApiResponse.success(shopService.createOrder(principal.getUserId(), request));
    }

    @GetMapping("/orders")
    @Operation(summary = "List current user's orders")
    public ApiResponse<List<ShopOrderResponse>> listOrders(@AuthenticationPrincipal AccountPrincipal principal,
                                                           @RequestParam(value = "status", required = false) String status,
                                                           @RequestParam(value = "page", required = false) Integer page,
                                                           @RequestParam(value = "size", required = false) Integer size) {
        return ApiResponse.success(shopService.listOrders(principal.getUserId(), status));
    }

    @GetMapping("/orders/by-user")
    @Operation(summary = "List orders by userId for Agent service")
    public ApiResponse<List<ShopOrderResponse>> listOrdersByUserId(
        @RequestParam(value = "userId") String userId,
        @RequestParam(value = "status", required = false) String status) {
        return ApiResponse.success(shopService.listOrders(userId, status));
    }

    @GetMapping("/orders/{orderNo}")
    @Operation(summary = "Get current user's order details")
    public ApiResponse<ShopOrderResponse> getOrder(@AuthenticationPrincipal AccountPrincipal principal,
                                                   @PathVariable String orderNo) {
        return ApiResponse.success(shopService.getOrder(principal.getUserId(), orderNo));
    }

    @PostMapping("/orders/{orderNo}/pay")
    @Operation(summary = "Pay current user's order with balance")
    public ApiResponse<PaymentResponse> pay(@AuthenticationPrincipal AccountPrincipal principal,
                                            @PathVariable String orderNo) {
        return ApiResponse.success(shopService.pay(principal.getUserId(), orderNo));
    }

    @GetMapping("/balance")
    @Operation(summary = "Get current user's balance")
    public ApiResponse<BalanceResponse> getBalance(@AuthenticationPrincipal AccountPrincipal principal) {
        return ApiResponse.success(shopService.getBalance(principal.getUserId()));
    }

    @GetMapping("/balance/transactions")
    @Operation(summary = "List current user's balance transactions")
    public ApiResponse<List<BalanceTransactionResponse>> listBalanceTransactions(
        @AuthenticationPrincipal AccountPrincipal principal,
        @RequestParam(value = "type", required = false) String type,
        @RequestParam(value = "page", required = false) Integer page,
        @RequestParam(value = "size", required = false) Integer size) {
        return ApiResponse.success(shopService.listBalanceTransactions(principal.getUserId(), type));
    }

    @PostMapping("/after-sales")
    @Operation(summary = "Create current user's after-sale request")
    public ApiResponse<ShopAfterSaleResponse> createAfterSale(
        @AuthenticationPrincipal AccountPrincipal principal,
        @Valid @RequestBody ShopAfterSaleCreateRequest request) {
        return ApiResponse.success(shopService.createAfterSale(principal.getUserId(), request));
    }

    @GetMapping("/after-sales")
    @Operation(summary = "List current user's after-sale requests")
    public ApiResponse<List<ShopAfterSaleResponse>> listAfterSales(@AuthenticationPrincipal AccountPrincipal principal) {
        return ApiResponse.success(shopService.listAfterSales(principal.getUserId()));
    }
}
