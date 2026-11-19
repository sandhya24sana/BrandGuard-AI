
const API_BASE = 'https://brandguard-ai-h6rw.onrender.com/api';

/**
 * Robust fetch utility that verifies response status and content-type
 * before attempting JSON parsing.
 */
async function safeFetch(url, options = {}) {
  try {
    const res = await fetch(url, options);
    const contentType = res.headers.get('content-type') || '';

    let data = null;

    if (contentType.includes('application/json')) {
      data = await res.json();
    } else {
      const rawText = await res.text();
      const preview = rawText
        .replace(/<[^>]+>/g, ' ')
        .replace(/\s+/g, ' ')
        .trim()
        .slice(0, 150);

      return {
        success: false,
        error: res.ok
          ? `Unexpected non-JSON response from server: ${preview}`
          : `HTTP ${res.status} (${res.statusText || 'Error'}): ${preview || 'Request failed'}`
      };
    }

    if (!res.ok) {
      return {
        success: false,
        error: data?.error || `Server returned error status HTTP ${res.status}`
      };
    }

    return data;
  } catch (err) {
    return {
      success: false,
      error: err.message || 'Network communication error'
    };
  }
}

export const api = {
  // System
  getHealth: async () => {
    return safeFetch(`${API_BASE}/health`);
  },

  getSystemStatus: async () => {
    return safeFetch(`${API_BASE}/analysis/system-status`);
  },

  // Brands
  getBrands: async () => {
    return safeFetch(`${API_BASE}/brands`);
  },

  getBrandDetails: async (brandId) => {
    if (!brandId) return { success: false, error: 'Brand ID is required' };
    return safeFetch(`${API_BASE}/brands/${brandId}`);
  },

  createBrand: async (data) => {
    return safeFetch(`${API_BASE}/brands`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data)
    });
  },

  deleteBrand: async (brandId) => {
    if (!brandId) return { success: false, error: 'Brand ID is required' };
    return safeFetch(`${API_BASE}/brands/${brandId}`, { method: 'DELETE' });
  },

  updateBrand: async (brandId, data) => {
    if (!brandId) return { success: false, error: 'Brand ID is required' };
    return safeFetch(`${API_BASE}/brands/${brandId}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data)
    });
  },

  // Guidelines Management
  addFact: async (brandId, data) => {
    return safeFetch(`${API_BASE}/brands/${brandId}/facts`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data)
    });
  },

  deleteFact: async (factId) => {
    return safeFetch(`${API_BASE}/brands/facts/${factId}`, {
      method: 'DELETE'
    });
  },

  addTerminology: async (brandId, data) => {
    return safeFetch(`${API_BASE}/brands/${brandId}/terminology`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data)
    });
  },

  deleteTerminology: async (termId) => {
    return safeFetch(`${API_BASE}/brands/terminology/${termId}`, {
      method: 'DELETE'
    });
  },

  updateVisualRules: async (brandId, data) => {
    return safeFetch(`${API_BASE}/brands/${brandId}/visual-rules`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data)
    });
  },

  addClaimRule: async (brandId, data) => {
    return safeFetch(`${API_BASE}/brands/${brandId}/claims`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data)
    });
  },

  deleteClaimRule: async (claimId) => {
    return safeFetch(`${API_BASE}/brands/claims/${claimId}`, {
      method: 'DELETE'
    });
  },

  // Content
  uploadFile: async (file) => {
    const formData = new FormData();
    formData.append('file', file);

    return safeFetch(`${API_BASE}/content/upload`, {
      method: 'POST',
      body: formData
    });
  },

  createContent: async (data) => {
    return safeFetch(`${API_BASE}/content`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data)
    });
  },

  getContentList: async (brandId, status) => {
    let url = `${API_BASE}/content?`;

    if (brandId) url += `brand_id=${brandId}&`;
    if (status && status !== 'ALL') url += `status=${status}&`;

    return safeFetch(url);
  },

  getContentDetail: async (contentId) => {
    if (!contentId) return { success: false, error: 'Content ID is required' };
    return safeFetch(`${API_BASE}/content/${contentId}`);
  },

  // Analysis
  quickCheck: async (data) => {
    return safeFetch(`${API_BASE}/analysis/quick-check`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data)
    });
  },

  analyzeContent: async (data) => {
    return safeFetch(`${API_BASE}/analysis/audit`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data)
    });
  },

  runAnalysis: async (contentId) => {
    if (!contentId) {
      return {
        success: false,
        error: 'Content ID is required to run analysis on saved item.'
      };
    }

    return safeFetch(`${API_BASE}/analysis/run/${contentId}`, {
      method: 'POST'
    });
  },

  applyCorrections: async (contentId, correctionRequest) => {
    if (!contentId) {
      return {
        success: false,
        error: 'Content ID is required to apply corrections.'
      };
    }

    const body = typeof correctionRequest === 'string'
      ? { corrected_text: correctionRequest }
      : correctionRequest;

    return safeFetch(`${API_BASE}/analysis/apply-corrections/${contentId}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body)
    });
  },

  // Reviews
  submitReview: async (data) => {
    return safeFetch(`${API_BASE}/reviews`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data)
    });
  },

  getRecentReviews: async () => {
    return safeFetch(`${API_BASE}/reviews/recent`);
  },

  // History & Analytics
  getDashboardStats: async (brandId) => {
    const url = brandId
      ? `${API_BASE}/history/dashboard-stats?brand_id=${brandId}`
      : `${API_BASE}/history/dashboard-stats`;

    return safeFetch(url);
  },

  exportAuditReport: async (contentId) => {
    if (!contentId) {
      return {
        success: false,
        error: 'Content ID is required to export report'
      };
    }

    return safeFetch(`${API_BASE}/history/export/${contentId}`);
  }
};
