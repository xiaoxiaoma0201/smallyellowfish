package com.smallyellowfish.ecommerce.dto;

import java.util.List;

public class CustomerServiceResponse {

    private final String answer;

    private final String sessionId;

    private final boolean confirmRequired;

    private final String pendingAction;

    private final String confirmationTitle;

    private final String confirmationSummary;

    private final String relatedOrderNo;

    private final String relatedAfterSaleNo;

    private final String resumeToken;

    private final List<CustomerServiceActionResponse> actions;

    private final boolean fallback;

    /** Agent 判定需要转人工客服时置为 true，商城前端据此展示"已转人工"标识。 */
    private boolean transferredToHuman;

    public CustomerServiceResponse(String answer, String sessionId, boolean confirmRequired, String pendingAction,
                                   String confirmationTitle, String confirmationSummary, String relatedOrderNo,
                                   String relatedAfterSaleNo, String resumeToken,
                                   List<CustomerServiceActionResponse> actions, boolean fallback) {
        this.answer = answer;
        this.sessionId = sessionId;
        this.confirmRequired = confirmRequired;
        this.pendingAction = pendingAction;
        this.confirmationTitle = confirmationTitle;
        this.confirmationSummary = confirmationSummary;
        this.relatedOrderNo = relatedOrderNo;
        this.relatedAfterSaleNo = relatedAfterSaleNo;
        this.resumeToken = resumeToken;
        this.actions = actions;
        this.fallback = fallback;
    }

    public boolean isTransferredToHuman() {
        return transferredToHuman;
    }

    public void setTransferredToHuman(boolean transferredToHuman) {
        this.transferredToHuman = transferredToHuman;
    }

    public String getAnswer() {
        return answer;
    }

    public String getSessionId() {
        return sessionId;
    }

    public boolean isConfirmRequired() {
        return confirmRequired;
    }

    public String getPendingAction() {
        return pendingAction;
    }

    public String getConfirmationTitle() {
        return confirmationTitle;
    }

    public String getConfirmationSummary() {
        return confirmationSummary;
    }

    public String getRelatedOrderNo() {
        return relatedOrderNo;
    }

    public String getRelatedAfterSaleNo() {
        return relatedAfterSaleNo;
    }

    public String getResumeToken() {
        return resumeToken;
    }

    public List<CustomerServiceActionResponse> getActions() {
        return actions;
    }

    public boolean isFallback() {
        return fallback;
    }
}
