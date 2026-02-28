const API_URL = "https://8ix9unryn4.execute-api.us-east-1.amazonaws.com"; // The user will find this in sam deploy output

const dropZone = document.getElementById('drop-zone');
const fileInput = document.getElementById('file-input');
const uploadBtn = document.getElementById('upload-btn');
const fileList = document.getElementById('file-list');

const downloadAllBtn = document.getElementById('download-all-btn');

let filesToUpload = [];
let processedFiles = []; // Track results: {name, csvUrl, imageUrl, jsonUrl, metadata}
let completedPolls = 0; // Track total files finished (success or error)
let stagedChanges = {}; // Local state for edits: { fileIdx: { data } }
let currentReviewIdx = 0;

// Drag and drop events
dropZone.onclick = () => fileInput.click();
dropZone.ondragover = (e) => {
    e.preventDefault();
    dropZone.classList.add('drag-over');
};
dropZone.ondragleave = () => dropZone.classList.remove('drag-over');
dropZone.ondrop = (e) => {
    e.preventDefault();
    dropZone.classList.remove('drag-over');
    handleFiles(e.dataTransfer.files);
};

fileInput.onchange = () => handleFiles(fileInput.files);

function handleFiles(files) {
    filesToUpload = Array.from(files);
    fileList.innerHTML = '';
    
    filesToUpload.forEach((file, index) => {
        const item = document.createElement('div');
        item.className = 'file-item';
        item.id = `file-${index}`;
        item.innerHTML = `
            <span>${file.name}</span>
            <span class="status">Ready to upload</span>
        `;
        fileList.appendChild(item);
    });

    if (filesToUpload.length > 0) {
        uploadBtn.disabled = false;
        // Strategy: Auto-start upload for better UX
        // startUpload(); // Removed auto-upload
    }
}

async function startUpload() {
    if (filesToUpload.length === 0) return;
    
    uploadBtn.disabled = true;
    downloadAllBtn.style.display = 'none';
    processedFiles = [];
    completedPolls = 0;
    
    const originalBtnText = uploadBtn.innerText;
    
    for (let i = 0; i < filesToUpload.length; i++) {
        uploadBtn.innerText = `Uploading ${i + 1} of ${filesToUpload.length}...`;
        const file = filesToUpload[i];
        const statusEl = document.querySelector(`#file-${i} .status`);
        
        try {
            updateStatus(statusEl, 'Uploading...', 'uploading'); // Initial status for this file
            
            // 1. Get Pre-signed URL from API
            const response = await fetch(`${API_URL}/upload-url?file=${encodeURIComponent(file.name)}`);
            if (!response.ok) throw new Error('Failed to get upload URL');
            const { upload_url } = await response.json();
            
            // 2. Upload file directly to S3 using the URL
            const uploadResponse = await fetch(upload_url, {
                method: 'PUT',
                body: file,
                headers: {
                    'Content-Type': file.type
                }
            });

            if (uploadResponse.ok) {
                // Start Polling for this specific file
                pollStatus(file, statusEl);
            } else {
                throw new Error('Upload failed');
            }
            
        } catch (error) {
            console.error(error);
            updateStatus(statusEl, 'Error: Upload Failed', 'status-error');
            completedPolls++;
            checkAllFinished();
        }
    }
    
    uploadBtn.innerText = "Upload Files";
    uploadBtn.disabled = false;
}

uploadBtn.onclick = startUpload;

async function pollStatus(file, statusEl) {
    updateStatus(statusEl, 'Processing with AI...', 'uploading'); // Update status after upload completes
    
    const interval = setInterval(async () => {
        try {
            const response = await fetch(`${API_URL}/status?file=${encodeURIComponent(file.name)}`);
            const data = await response.json();
            
            if (data.error) {
                clearInterval(interval);
                completedPolls++;
                updateStatus(statusEl, 'Error: Backend error', 'status-error');
                console.error(`API Error for ${file.name}:`, data.error);
                checkAllFinished();
                return;
            }
            
            if (data.status === 'complete') {
                clearInterval(interval);
                
                // Prevent duplicate processing if already added
                if (processedFiles.some(f => f.name === file.name)) {
                    console.warn(`Duplicate processing detected for ${file.name}`);
                    return;
                }
                
                completedPolls++;

                updateStatus(statusEl, 'Finished!', 'status-success', processedFiles.length); // Pass current index
                
                processedFiles.push({
                    name: file.name,
                    csv: data.download_urls.csv,
                    image: data.download_urls.image,
                    json: data.download_urls.json,
                    metadata: data.metadata,
                    id: processedFiles.length // Store index for review lookup
                });
                
                checkAllFinished();
            } else if (data.status === 'error') {
                clearInterval(interval);
                completedPolls++;
                
                let userMsg = "Backend Error";
                if (data.message && data.message.includes("No JSON found")) {
                    userMsg = "AI could not read ticket clearly";
                } else if (data.message) {
                    userMsg = data.message;
                }
                
                updateStatus(statusEl, 'Error: ' + userMsg, 'status-error');
                console.error(`Processing error for ${file.name}:`, data.message);
                checkAllFinished();
            }
        } catch (e) {
            console.error("Polling error:", e);
        }
    }, 3000); // Check every 3 seconds
}

function checkAllFinished() {
    if (completedPolls === filesToUpload.length && processedFiles.length > 0) {
        document.getElementById('review-all-btn').style.display = 'block';
    }
}

document.getElementById('review-all-btn').onclick = () => {
    openReviewModal(0);
};

downloadAllBtn.onclick = async () => {
    downloadAllBtn.innerText = "Zipping results...";
    downloadAllBtn.disabled = true;
    
    const zip = new JSZip();
    for (const file of processedFiles) {
        // Hierarchical structure based on image_key (e.g., "2026/01/2026-01-25_CEMEX_123.jpg")
        const key = file.metadata.image_key;
        const parts = key.split('/'); // ["2026", "01", "2026-01-25_CEMEX_123.jpg"]
        const year = parts[0];
        const month = parts[1];
        const ticketFolderBase = file.metadata.renamed_base;
        
        // Target: Year/Month/Ticket_ID/
        const folderPath = `${year}/${month}/${ticketFolderBase}`;
        const targetFolder = zip.folder(folderPath);

        const base = file.metadata.renamed_base;
        
        // Fetch and add CSV
        if (file.csv) await addFileToZip(targetFolder, `${base}.csv`, file.csv);
        // Fetch and add JSON
        if (file.json) await addFileToZip(targetFolder, `${base}.json`, file.json);
        // Fetch and add Image
        if (file.image) await addFileToZip(targetFolder, parts[parts.length - 1], file.image);
    }

    const content = await zip.generateAsync({ type: "blob" });
    const link = document.createElement("a");
    link.href = URL.createObjectURL(content);
    link.download = `weigh_tickets_batch_${new Date().getTime()}.zip`;
    link.click();
    
    downloadAllBtn.innerText = "Download All Results (.zip)";
    downloadAllBtn.disabled = false;
};

async function addFileToZip(folder, name, url) {
    try {
        const response = await fetch(url);
        if (!response.ok) {
            console.error(`Fetch failed for ${name}: ${response.status} ${response.statusText}`);
            return;
        }
        const blob = await response.blob();
        folder.file(name, blob);
    } catch (e) {
        console.error(`Failed to add ${name} to zip`, e);
    }
}

function updateStatus(el, text, className, fileIdx = null) {
    el.innerText = text;
    el.className = `status ${className}`;
}

// --- Review Wizard Logic ---

async function openReviewModal(idx) {
    // Save current fields before switching
    if (currentReviewIdx !== null && document.getElementById('review-modal').style.display === 'block') {
        saveToStaging(currentReviewIdx);
    }

    currentReviewIdx = idx;
    const file = processedFiles[idx];
    
    // Update UI
    document.getElementById('review-modal').style.display = 'block';
    document.getElementById('modal-image').src = file.image;
    document.getElementById('review-counter').innerText = `Item ${idx + 1} of ${processedFiles.length}`;
    
    // Reset rotation for new image
    currentRotation = 0;
    document.getElementById('modal-image').style.transform = 'none';
    document.getElementById('modal-image').src = file.image;
    
    // Button Logic
    document.getElementById('prev-btn').disabled = (idx === 0);
    const nextBtn = document.getElementById('next-btn');
    const finalizeBtn = document.getElementById('finalize-btn');

    if (idx === processedFiles.length - 1) {
        nextBtn.style.display = 'none';
        finalizeBtn.style.display = 'block';
    } else {
        nextBtn.style.display = 'block';
        finalizeBtn.style.display = 'none';
    }

    const form = document.getElementById('modal-form');
    
    // Check if we have staged changes, else load from URL
    if (stagedChanges[idx]) {
        renderForm(stagedChanges[idx]);
    } else {
        form.innerHTML = '<p>Loading JSON...</p>';
        try {
            const response = await fetch(file.json);
            const data = await response.json();
            stagedChanges[idx] = data;
            renderForm(data);
        } catch (e) {
            form.innerHTML = '<p style="color:red">Error loading data</p>';
        }
    }
}

let currentRotation = 0;
function rotateImage() {
    currentRotation = (currentRotation + 90) % 360;
    const img = document.getElementById('modal-image');
    const originalSrc = processedFiles[currentReviewIdx].image;
    
    // Use an offscreen image and Canvas to natively swap the aspect ratio and pixels
    const offscreenImg = new Image();
    offscreenImg.crossOrigin = "Anonymous";
    offscreenImg.onload = () => {
        const canvas = document.createElement('canvas');
        if (currentRotation % 180 !== 0) {
            canvas.width = offscreenImg.height;
            canvas.height = offscreenImg.width;
        } else {
            canvas.width = offscreenImg.width;
            canvas.height = offscreenImg.height;
        }
        
        const ctx = canvas.getContext('2d');
        ctx.translate(canvas.width / 2, canvas.height / 2);
        ctx.rotate(currentRotation * Math.PI / 180);
        ctx.drawImage(offscreenImg, -offscreenImg.width / 2, -offscreenImg.height / 2);
        
        img.src = canvas.toDataURL('image/jpeg', 0.9);
    };
    offscreenImg.src = originalSrc;
}

function renderForm(data) {
    const form = document.getElementById('modal-form');
    form.innerHTML = '';
    Object.keys(data).forEach(key => {
        if (['scan_path', 'processed_at', 'id'].includes(key)) return;
        
        let value = data[key];
        let confidence = 100;

        // Handle nested confidence object
        if (typeof value === 'object' && value !== null && 'value' in value) {
            confidence = value.confidence;
            value = value.value;
        }

        const div = document.createElement('div');
        div.className = 'form-group';
        
        let labelHtml = `<label>${key.replace(/_/g, ' ').toUpperCase()}</label>`;
        let inputClass = '';
        let warningHtml = '';

        if (confidence < 80) {
            inputClass = 'low-confidence';
            warningHtml = `<span style="color: #ef4444; font-size: 0.8rem; margin-left: 5px;" title="Confidence: ${confidence}%">⚠️ Check this (Confidence: ${confidence}%)</span>`;
            labelHtml = `<label style="color: #ef4444;">${key.replace(/_/g, ' ').toUpperCase()} ${warningHtml}</label>`;
        }

        div.innerHTML = `
            ${labelHtml}
            <input type="text" id="field-${key}" value="${value}" class="${inputClass}" data-confidence="${confidence}">
        `;
        form.appendChild(div);
    });
}

function saveToStaging(idx) {
    const inputs = document.querySelectorAll('#modal-form input');
    if (inputs.length === 0) return;
    
    // We need to preserve the structure (value + confidence)
    // But since the user edited it, it's now 100% confident (human reviewed)
    const updatedData = { ...stagedChanges[idx] };
    
    inputs.forEach(input => {
        const key = input.id.replace('field-', '');
        // We save it back as the nested object to maintain schema consistency
        updatedData[key] = {
            value: input.value,
            confidence: 100 // User verified
        };
    });
    stagedChanges[idx] = updatedData;
}

function nextItem() {
    if (currentReviewIdx < processedFiles.length - 1) {
        openReviewModal(currentReviewIdx + 1);
    }
}

function prevItem() {
    if (currentReviewIdx > 0) {
        openReviewModal(currentReviewIdx - 1);
    }
}

function closeModal() {
    document.getElementById('review-modal').style.display = 'none';
}

async function finalizeBatch() {
    // Save the last item first
    saveToStaging(currentReviewIdx);
    
    const finalizeBtn = document.getElementById('finalize-btn');
    const statusDiv = document.getElementById('save-status');
    
    finalizeBtn.disabled = true;
    statusDiv.style.display = 'block';
    statusDiv.innerText = "Syncing all changes to S3...";

    try {
        // Parallel sync all corrected data to S3
        const syncPromises = processedFiles.map((file, idx) => {
            return fetch(`${API_URL}/save`, {
                method: 'POST',
                body: JSON.stringify({
                    csv_key: file.metadata.csv_key,
                    json_key: file.metadata.json_key,
                    data: stagedChanges[idx]
                })
            });
        });

        await Promise.all(syncPromises);
        
        statusDiv.innerText = "Generating ZIP...";
        await downloadZip();
        
        statusDiv.innerText = "Batch Successfully Finalized!";
        setTimeout(() => closeModal(), 2000);
        
    } catch (e) {
        alert("Batch sync failed: " + e.message);
        finalizeBtn.disabled = false;
        statusDiv.style.display = 'none';
    }
}

async function downloadZip() {
    const zip = new JSZip();
    for (const file of processedFiles) {
        const key = file.metadata.image_key;
        const parts = key.split('/');
        const year = parts[0];
        const month = parts[1];
        const ticketFolderBase = file.metadata.renamed_base;
        
        const folderPath = `${year}/${month}/${ticketFolderBase}`;
        const targetFolder = zip.folder(folderPath);

        const base = file.metadata.renamed_base;
        
        // CSV & JSON from Staging (to ensure we zip the CURRENT edits even if fetch is cached)
        // Wait, the zip function fetches from URLs. 
        // We should add files directly from stagedChanges for JSON/CSV to be 100% sure.
        
        // 1. Original Image (still from URL)
        await addFileToZip(targetFolder, parts[parts.length - 1], file.image);
        
        // 2. JSON (from local state)
        const idx = processedFiles.indexOf(file);
        targetFolder.file(`${base}.json`, JSON.stringify(stagedChanges[idx], null, 2));
        
        // 3. CSV (Generate simple CSV from state)
        const csvContent = generateCSV(stagedChanges[idx]);
        targetFolder.file(`${base}.csv`, csvContent);
    }

    const content = await zip.generateAsync({ type: "blob" });
    const link = document.createElement("a");
    link.href = URL.createObjectURL(content);
    link.download = `tickets_batch_${new Date().toLocaleDateString().replace(/\//g, '-')}.zip`;
    link.click();
}

function generateCSV(data) {
    const headers = Object.keys(data).filter(k => !['scan_path', 'processed_at', 'id'].includes(k));
    const row = headers.map(h => {
        let val = data[h];
        if (typeof val === 'object' && val !== null && 'value' in val) {
            val = val.value;
        }
        return `"${String(val).replace(/"/g, '""')}"`;
    }).join(',');
    return `${headers.join(',')}\n${row}`;
}
