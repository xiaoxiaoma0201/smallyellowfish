package com.smallyellowfish.ecommerce.dto;

/** 卖家发货请求参数；物流单号为空时由后端自动生成。 */
public record ShipOrderRequest(
    String logisticsNo
) {
}
