package com.smallyellowfish.ecommerce.controller;

import com.smallyellowfish.ecommerce.dto.ApiResponse;
import com.smallyellowfish.ecommerce.dto.ApprovalCreateRequest;
import com.smallyellowfish.ecommerce.dto.ApprovalDecisionRequest;
import com.smallyellowfish.ecommerce.dto.ApprovalResponse;
import com.smallyellowfish.ecommerce.security.AccountPrincipal;
import com.smallyellowfish.ecommerce.service.ApprovalService;
import io.swagger.v3.oas.annotations.Operation;
import io.swagger.v3.oas.annotations.tags.Tag;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.security.core.Authentication;

@Tag(name = "Approvals", description = "小黄鱼二手电商交易平台人工审批模拟接口，用于 HITL 流程")
@RestController
@RequestMapping("/api/approvals")
public class ApprovalController {

    private final ApprovalService approvalService;

    public ApprovalController(ApprovalService approvalService) {
        this.approvalService = approvalService;
    }

    @GetMapping("/{approvalId}")
    @Operation(summary = "Get approval")
    public ApiResponse<ApprovalResponse> getApproval(@PathVariable String approvalId) {
        return ApiResponse.success(approvalService.getByApprovalId(approvalId));
    }

    @PostMapping
    @Operation(summary = "Create approval")
    public ApiResponse<ApprovalResponse> createApproval(@RequestBody ApprovalCreateRequest request) {
        return ApiResponse.success(approvalService.create(request));
    }

    @PostMapping("/{approvalId}/approve")
    @Operation(summary = "Approve pending request")
    public ApiResponse<ApprovalResponse> approve(
        @PathVariable String approvalId,
        @RequestBody ApprovalDecisionRequest request,
        Authentication authentication) {
        request.setOperator(authenticatedOperator(authentication));
        return ApiResponse.success(approvalService.approve(approvalId, request));
    }

    @PostMapping("/{approvalId}/reject")
    @Operation(summary = "Reject pending request")
    public ApiResponse<ApprovalResponse> reject(
        @PathVariable String approvalId,
        @RequestBody ApprovalDecisionRequest request,
        Authentication authentication) {
        request.setOperator(authenticatedOperator(authentication));
        return ApiResponse.success(approvalService.reject(approvalId, request));
    }

    private String authenticatedOperator(Authentication authentication) {
        if (authentication != null && authentication.getPrincipal() instanceof AccountPrincipal principal) {
            return principal.getUsername();
        }
        return authentication == null ? "" : authentication.getName();
    }
}
