// === НАЧАЛО КЛАССА PENDULUM PAINT SIMULATOR ДЛЯ РЕЖИМА РАМКИ ===
class PendulumPaintSimulator {
    constructor() {
        // === НАЧАЛО ИНИЦИАЛИЗАЦИИ ПЕРЕМЕННЫХ ===
        this.canvas = document.getElementById('canvas');
        this.ctx = this.canvas.getContext('2d');
        
        this.svgPathData = '';
        this.time = 0;
        this.isRunning = false;
        this.pointsDrawn = 0;
        this.lastFrameTime = 0;
        this.fps = 0;
        
        this.maxAmplitude = 100;
        this.dpi = 1200;
        this.mmToPx = this.dpi / 25.4;
        
        this.params = {
            canvasAmplitudeX: 55,
            canvasAmplitudeY: 55,
            canvasFreqX: 0.9,
            canvasFreqY: 1.1,
            bucketAmplitudeX: 65,
            bucketAmplitudeY: 65,
            bucketFreqX: 2.4,
            bucketFreqY: 1.8,
            damping: 0.0002,
            angularVelocity: 0.25,
            timeSpeed: 1.0,
            lineWidth: 1.2
        };
        
        this.frameParams = {
            paperFormat: 'A4',
            margin: 20,
            frameWidth: 30,
            direction: 'counterclockwise',
            periodicity: 240,
            iterations: 1
        };
        
        this.lastX = null;
        this.lastY = null;
        this.framePath = [];
        this.framePosition = 0;
        this.currentIteration = 0;
        this.lastSegment = -1;
        // === КОНЕЦ ИНИЦИАЛИЗАЦИИ ПЕРЕМЕННЫХ ===

        this.initControls();
        this.initButtons();
        this.setupCanvas();
        this.animate();
    }
    
    // === НАЧАЛО МЕТОДОВ ИНИЦИАЛИЗАЦИИ ===
    initControls() {
        this.initMainControls();
        this.initFrameControls();
    }

    initMainControls() {
        const params = this.params;
        Object.keys(params).forEach(param => {
            const slider = document.getElementById(param);
            const input = document.getElementById(param + 'Input');
            const valueDisplay = document.getElementById(param + 'Value');
            
            if (slider && valueDisplay) {
                slider.max = this.maxAmplitude;
                if (input) input.max = this.maxAmplitude;
                
                slider.value = params[param];
                valueDisplay.textContent = params[param];
                if (input) input.value = params[param];
                
                slider.addEventListener('input', (e) => {
                    params[param] = parseFloat(e.target.value);
                    valueDisplay.textContent = params[param];
                    if (input) input.value = params[param];
                });
            }
            
            if (input) {
                input.addEventListener('input', (e) => {
                    const value = parseFloat(e.target.value);
                    if (!isNaN(value)) {
                        const clampedValue = Math.min(Math.max(value, 0), this.maxAmplitude);
                        params[param] = clampedValue;
                        if (slider) slider.value = clampedValue;
                        if (valueDisplay) valueDisplay.textContent = clampedValue;
                        input.value = clampedValue;
                    }
                });
            }
        });
    }

    initFrameControls() {
        const paperFormat = document.getElementById('paperFormat');
        const marginInput = document.getElementById('marginInput');
        const frameWidthInput = document.getElementById('frameWidthInput');
        const directionSwitch = document.getElementById('directionSwitch');
        const periodicityInput = document.getElementById('periodicityInput');
        const iterationsInput = document.getElementById('iterationsInput');
        
        paperFormat.value = this.frameParams.paperFormat;
        marginInput.value = this.frameParams.margin;
        frameWidthInput.value = this.frameParams.frameWidth;
        directionSwitch.value = this.frameParams.direction;
        periodicityInput.value = this.frameParams.periodicity;
        iterationsInput.value = this.frameParams.iterations;
        
        paperFormat.addEventListener('change', (e) => {
            this.frameParams.paperFormat = e.target.value;
        });
        
        marginInput.addEventListener('input', (e) => {
            this.frameParams.margin = parseFloat(e.target.value);
        });
        
        frameWidthInput.addEventListener('input', (e) => {
            this.frameParams.frameWidth = parseFloat(e.target.value);
        });
        
        directionSwitch.addEventListener('change', (e) => {
            this.frameParams.direction = e.target.value;
        });
        
        periodicityInput.addEventListener('input', (e) => {
            this.frameParams.periodicity = parseFloat(e.target.value);
        });
        
        iterationsInput.addEventListener('input', (e) => {
            this.frameParams.iterations = parseInt(e.target.value);
        });
    }
    
    initButtons() {
        document.getElementById('startBtn').addEventListener('click', () => {
            this.isRunning = true;
            this.prepareFrameMode();
        });
        
        document.getElementById('pauseBtn').addEventListener('click', () => {
            this.isRunning = false;
        });
        
        document.getElementById('clearBtn').addEventListener('click', () => {
            this.clearCanvas();
        });
        
        document.getElementById('saveBtn').addEventListener('click', () => {
            this.saveSVG();
        });
    }
    
    setupCanvas() {
        this.ctx.fillStyle = '#000';
        this.ctx.fillRect(0, 0, this.canvas.width, this.canvas.height);
        this.ctx.strokeStyle = 'white';
        this.ctx.lineCap = 'round';
        this.ctx.lineJoin = 'round';
    }
    // === КОНЕЦ МЕТОДОВ ИНИЦИАЛИЗАЦИИ ===

    // === НАЧАЛО МЕТОДОВ РЕЖИМА РАМКИ ===
    prepareFrameMode() {
        this.calculateFramePath();
        this.framePosition = 0;
        this.currentIteration = 0;
        this.svgPathData = '';
        this.clearCanvas();
    }

    calculateFramePath() {
        const paperSizes = {
            'A4': { width: 210, height: 297 },
            'A3': { width: 297, height: 420 },
            'A2': { width: 420, height: 594 },
            'custom': { width: 297, height: 420 } // По умолчанию A4 для произвольного формата
        };
        
        const size = paperSizes[this.frameParams.paperFormat] || { width: 210, height: 297 };
        
        const widthPx = size.width * this.mmToPx;
        const heightPx = size.height * this.mmToPx;
        const marginPx = this.frameParams.margin * this.mmToPx;
        const frameWidthPx = this.frameParams.frameWidth * this.mmToPx;
        
        const innerX = marginPx + frameWidthPx / 2;
        const innerY = marginPx + frameWidthPx / 2;
        const outerX = widthPx - marginPx - frameWidthPx / 2;
        const outerY = heightPx - marginPx - frameWidthPx / 2;
        
        this.framePath = [];
        
        if (this.frameParams.direction === 'counterclockwise') {
            this.framePath.push(...this.generateVerticalPath(innerX, innerY, outerY, 'down'));
            this.framePath.push(...this.generateHorizontalPath(outerY, innerX, outerX, 'right'));
            this.framePath.push(...this.generateVerticalPath(outerX, outerY, innerY, 'up'));
            this.framePath.push(...this.generateHorizontalPath(innerY, outerX, innerX, 'left'));
        } else {
            this.framePath.push(...this.generateHorizontalPath(innerY, innerX, outerX, 'right'));
            this.framePath.push(...this.generateVerticalPath(outerX, innerY, outerY, 'down'));
            this.framePath.push(...this.generateHorizontalPath(outerY, outerX, innerX, 'left'));
            this.framePath.push(...this.generateVerticalPath(innerX, outerY, innerY, 'up'));
        }
    }

    generateVerticalPath(x, startY, endY, direction) {
        const path = [];
        const step = (direction === 'down' ? 1 : -1) * 5;
        
        for (let y = startY; direction === 'down' ? y <= endY : y >= endY; y += step) {
            path.push({ x, y });
        }
        
        return path;
    }

    generateHorizontalPath(y, startX, endX, direction) {
        const path = [];
        const step = (direction === 'right' ? 1 : -1) * 5;
        
        for (let x = startX; direction === 'right' ? x <= endX : x >= endX; x += step) {
            path.push({ x, y });
        }
        
        return path;
    }

    calculateScalingFactors() {
        const paperSizes = {
            'A4': { width: 210, height: 297 },
            'A3': { width: 297, height: 420 },
            'A2': { width: 420, height: 594 },
            'custom': { width: 297, height: 420 }
        };
        
        const size = paperSizes[this.frameParams.paperFormat] || { width: 210, height: 297 };
        const maxDimension = Math.max(size.width, size.height);
        
        this.scaleX = (size.width / maxDimension) * 0.9;
        this.scaleY = (size.height / maxDimension) * 0.9;
    }
    // === КОНЕЦ МЕТОДОВ РЕЖИМА РАМКИ ===

    // === НАЧАЛО МЕТОДОВ РАСЧЕТА ПОЗИЦИИ ===
    calculatePosition(time) {
        return this.calculateFramePosition(time);
    }
    
    calculateRosettePosition(time) {
        const centerX = this.canvas.width / 2;
        const centerY = this.canvas.height / 2;
        const p = this.params;
        
        const dampingFactor = Math.exp(-p.damping * time);
        const angleRad = p.angularVelocity * time;
        const cos = Math.cos(angleRad);
        const sin = Math.sin(angleRad);
        
        const canvasX = p.canvasAmplitudeX * dampingFactor * Math.sin(p.canvasFreqX * time);
        const canvasY = p.canvasAmplitudeY * dampingFactor * Math.cos(p.canvasFreqY * time);
        
        const bucketXRaw = p.bucketAmplitudeX * dampingFactor * Math.cos(p.bucketFreqX * time);
        const bucketYRaw = p.bucketAmplitudeY * dampingFactor * Math.sin(p.bucketFreqY * time);
        
        const bucketX = bucketXRaw * cos - bucketYRaw * sin;
        const bucketY = bucketXRaw * sin + bucketYRaw * cos;
        
        const x = centerX + (bucketX - canvasX);
        const y = centerY + (bucketY - canvasY);
        
        return { x, y, bucketX, bucketY, canvasX, canvasY };
    }
    
    calculateFramePosition(time) {
        if (this.framePath.length === 0) return this.calculateRosettePosition(time);
        
        const speed = this.frameParams.periodicity > 0 ? 
            (this.framePath.length / (this.frameParams.periodicity * 60)) : 1;
        
        this.framePosition = (this.framePosition + speed) % this.framePath.length;
        
        const framePos = this.framePath[Math.floor(this.framePosition)];
        const rosettePos = this.calculateRosettePosition(time);
        
        return {
            x: framePos.x + (rosettePos.bucketX - rosettePos.canvasX),
            y: framePos.y + (rosettePos.bucketY - rosettePos.canvasY),
            bucketX: rosettePos.bucketX,
            bucketY: rosettePos.bucketY
        };
    }
    // === КОНЕЦ МЕТОДОВ РАСЧЕТА ПОЗИЦИИ ===

    // === НАЧАЛО МЕТОДОВ ОТРИСОВКИ ===
    updatePendulumVisualization(bucketX, bucketY) {
        const rope = document.getElementById('rope');
        const bucket = document.getElementById('bucket');
        const drop = document.getElementById('drop');
        
        if (rope && bucket && drop) {
            const ropeX = 75 + bucketX * 0.2;
            const ropeY = 50 + bucketY * 0.15;
            
            rope.setAttribute('x2', ropeX);
            rope.setAttribute('y2', ropeY);
            bucket.setAttribute('x', ropeX - 10);
            bucket.setAttribute('y', ropeY - 5);
            drop.setAttribute('cx', ropeX);
            drop.setAttribute('cy', ropeY + 10);
        }
    }
    
    drawPoint(x, y) {
        this.drawFramePoint(x, y);
    }
    
    drawFramePoint(x, y) {
        // Преобразуем абсолютные координаты рамки в координаты canvas
        const paperSizes = {
            'A4': { width: 210, height: 297 },
            'A3': { width: 297, height: 420 },
            'A2': { width: 420, height: 594 },
            'custom': { width: 297, height: 420 }
        };
        
        const size = paperSizes[this.frameParams.paperFormat] || { width: 210, height: 297 };
        const widthPx = size.width * this.mmToPx;
        const heightPx = size.height * this.mmToPx;
        
        // Масштабируем координаты под размер canvas
        const scaleX = this.canvas.width / widthPx;
        const scaleY = this.canvas.height / heightPx;
        const scale = Math.min(scaleX, scaleY) * 0.9; // 90% от масштаба
        
        const canvasX = x * scale;
        const canvasY = y * scale;
        
        this.ctx.lineWidth = this.params.lineWidth;
        
        if (this.lastX !== null && this.lastY !== null) {
            this.ctx.beginPath();
            this.ctx.moveTo(this.lastX, this.lastY);
            this.ctx.lineTo(canvasX, canvasY);
            this.ctx.stroke();
            
            this.svgPathData += ` L ${x.toFixed(2)} ${y.toFixed(2)}`;
        } else {
            this.svgPathData = `M ${x.toFixed(2)} ${y.toFixed(2)}`;
            this.ctx.fillStyle = '#000';
            this.ctx.fillRect(0, 0, this.canvas.width, this.canvas.height);
        }
        
        this.lastX = canvasX;
        this.lastY = canvasY;
        this.pointsDrawn++;
        
        this.updateDisplay();
    }
    
    updateDisplay() {
        document.getElementById('pointsCount').textContent = this.pointsDrawn;
        document.getElementById('pathLength').textContent = this.pointsDrawn;
        
        const positionDisplay = document.getElementById('positionDisplay');
        if (this.framePath.length > 0) {
            const progress = ((this.framePosition / this.framePath.length) * 100).toFixed(1);
            positionDisplay.textContent = `Итер. ${this.currentIteration + 1}: ${progress}%`;
        } else {
            positionDisplay.textContent = 'Центр';
        }
    }
    // === КОНЕЦ МЕТОДОВ ОТРИСОВКИ ===

    // === НАЧАЛО МЕТОДОВ УПРАВЛЕНИЯ ===
    clearCanvas() {
        this.ctx.fillStyle = '#000';
        this.ctx.fillRect(0, 0, this.canvas.width, this.canvas.height);
        
        this.svgPathData = '';
        this.lastX = null;
        this.lastY = null;
        this.pointsDrawn = 0;
        this.time = 0;
        this.framePosition = 0;
        this.currentIteration = 0;
        
        document.getElementById('timeDisplay').textContent = '0.00';
        document.getElementById('pointsCount').textContent = '0';
        document.getElementById('pathLength').textContent = '0';
        document.getElementById('positionDisplay').textContent = 'Центр';
    }
    
    saveSVG() {
        if (!this.svgPathData) {
            alert('Нет данных для сохранения!');
            return;
        }
        
        const paperSizes = {
            'A4': { width: 210, height: 297 },
            'A3': { width: 297, height: 420 },
            'A2': { width: 420, height: 594 },
            'custom': { width: 297, height: 420 }
        };
        
        const size = paperSizes[this.frameParams.paperFormat] || { width: 210, height: 297 };
        
        const svgContent = `<?xml version="1.0" encoding="UTF-8"?>
<svg width="${size.width}mm" height="${size.height}mm" viewBox="0 0 ${size.width} ${size.height}" xmlns="http://www.w3.org/2000/svg">
  <path d="${this.svgPathData}" stroke="black" stroke-width="${this.params.lineWidth * 0.2}" fill="none" 
        stroke-linecap="round" stroke-linejoin="round"/>
</svg>`;
        
        const blob = new Blob([svgContent], { type: 'image/svg+xml' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `guilloche-frame-${Date.now()}.svg`;
        a.click();
        URL.revokeObjectURL(url);
    }
    // === КОНЕЦ МЕТОДОВ УПРАВЛЕНИЯ ===

    // === НАЧАЛО ГЛАВНОГО ЦИКЛА АНИМАЦИИ ===
    animate(currentTime = 0) {
        if (this.lastFrameTime) {
            this.fps = Math.round(1000 / (currentTime - this.lastFrameTime));
        }
        this.lastFrameTime = currentTime;
        
        if (this.isRunning) {
            this.time += 0.016 * this.params.timeSpeed;
            
            const pos = this.calculatePosition(this.time);
            this.updatePendulumVisualization(pos.bucketX, pos.bucketY);
            this.drawPoint(pos.x, pos.y);
            
            document.getElementById('timeDisplay').textContent = this.time.toFixed(2);
            document.getElementById('fpsDisplay').textContent = this.fps;
        }
        
        requestAnimationFrame((time) => this.animate(time));
    }
    // === КОНЕЦ ГЛАВНОГО ЦИКЛА АНИМАЦИИ ===
}

// === НАЧАЛО ИНИЦИАЛИЗАЦИИ ПРИЛОЖЕНИЯ ===
document.addEventListener('DOMContentLoaded', () => {
    new PendulumPaintSimulator();
});
// === КОНЕЦ ИНИЦИАЛИЗАЦИИ ПРИЛОЖЕНИЯ ===